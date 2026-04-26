# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

# mypy: ignore-errors
"""AGE-49 编译验证脚本单测"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "age8_compile_check.py"


def _load_script_module() -> object:
    """import 脚本作为模块（脚本在 scripts/ 不是 package）"""
    import sys
    spec = importlib.util.spec_from_file_location(
        "age8_compile_check", SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["age8_compile_check"] = mod  # dataclass 需要在 sys.modules 找到
    spec.loader.exec_module(mod)
    return mod


_compile_check = _load_script_module()


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


class TestCompileReport:
    def test_python_pass(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.py"
        f.write_text("def foo():\n    return 1\n")
        report = _compile_check.run_compile_check(tmp_path)
        assert report.total == 1
        assert report.syntax_clean == 1
        assert report.results[0].status == "syntax_clean"

    def test_python_fail(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("def foo(:\n  return 1\n")
        report = _compile_check.run_compile_check(tmp_path)
        # python 的 syntax error 落到 syntax_dirty 或 sysroot_missing；
        # 用 status 字段比 .failed 更精准
        result = report.results[0]
        assert result.status in ("syntax_dirty", "sysroot_missing")
        assert result.error

    def test_unsupported_extension_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "config.toml"
        f.write_text("a = 1\n")
        report = _compile_check.run_compile_check(tmp_path)
        # toml 不在支持列表 → 被忽略（不进 results）
        assert report.total == 0

    def test_unknown_extension_explicit_skip(self, tmp_path: Path) -> None:
        """relative_paths 显式指定时，未支持扩展名 → status=skipped"""
        f = tmp_path / "x.xyz"
        f.write_text("noop\n")
        report = _compile_check.run_compile_check(
            tmp_path, relative_paths=["x.xyz"],
        )
        assert report.skipped == 1
        assert report.results[0].status == "skipped"

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        report = _compile_check.run_compile_check(
            tmp_path, relative_paths=["nope.py"],
        )
        assert report.skipped == 1
        assert "not found" in report.results[0].skip_reason

    @pytest.mark.skipif(not _has_tool("g++"), reason="g++ 不在 PATH")
    def test_cpp_pass(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.cc"
        f.write_text(
            "int main() { return 0; }\n",
        )
        report = _compile_check.run_compile_check(tmp_path)
        assert report.syntax_clean == 1

    @pytest.mark.skipif(not _has_tool("g++"), reason="g++ 不在 PATH")
    def test_cpp_fail(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.cc"
        f.write_text("int main(\n")  # 不闭合
        report = _compile_check.run_compile_check(tmp_path)
        # 编译失败：可能是 syntax_dirty（cascade 错）或 sysroot_missing
        # 关键是 status 不是 syntax_clean
        result = report.results[0]
        assert result.status in ("syntax_dirty", "sysroot_missing")
        assert result.error

    def test_tool_availability_reported(self, tmp_path: Path) -> None:
        report = _compile_check.run_compile_check(tmp_path)
        assert "g++" in report.tool_availability
        assert "gcc" in report.tool_availability


class TestIndexUpdate:
    def test_index_compile_status_filled(self, tmp_path: Path) -> None:
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        ok = files_dir / "a.py"
        ok.write_text("x = 1\n")
        bad = files_dir / "b.py"
        bad.write_text("def\n")  # syntax error

        index_path = tmp_path / "files-index.json"
        index_path.write_text(json.dumps([
            {"path": "a.py", "filename": "a.py", "language": "python"},
            {"path": "b.py", "filename": "b.py", "language": "python"},
        ]))

        report = _compile_check.run_compile_check(
            files_dir, relative_paths=["a.py", "b.py"],
        )
        _compile_check.update_index_with_compile(index_path, report)
        idx = json.loads(index_path.read_text())
        assert idx[0]["compile_status"] == "syntax_clean"
        assert idx[1]["compile_status"] in ("syntax_dirty", "sysroot_missing")
        assert idx[1].get("compile_error")


class TestSpikeIntegration:
    """集成：跑 spike render 出来的 5 个文件"""

    def test_spike_render_files(self, tmp_path: Path) -> None:
        files_dir = (
            PROJECT_ROOT / "output" / "age8-render-fixed" / "files"
        )
        if not files_dir.exists():
            pytest.skip("spike render 输出不存在")
        report = _compile_check.run_compile_check(files_dir)
        # 实测：spike 真文件大概率 g++ syntax 失败（缺 #include 路径）
        # 但脚本本身不应崩溃 + 应给出明确报告
        assert report.total >= 4
        # 至少 .py / .gn / .h / .cc 的某种应该被分类
        statuses = {r.status for r in report.results}
        assert statuses, "没有任何结果"
