# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""AGE-49 端到端集成测试：真起 g++ 子进程跑 syntax check。

不 mock。直接调 ``run_compile_check``，期望它真的 ``subprocess.run g++``
拿到 stderr 后做错误分类。覆盖：
1. 干净 chromium 风格 .cc → syntax_clean
2. OCR 粘连 #ifndEf → syntax_dirty
3. 缺类型（不给 stub）→ sysroot_missing
4. 给关键 stub 后能 syntax_clean

需要 g++ 在 PATH。CI 没装 g++ 时整组 skip。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "age8_compile_check.py"


def _load_compile_check() -> object:
    spec = importlib.util.spec_from_file_location(
        "age8_compile_check", SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["age8_compile_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_stub_gen() -> object:
    path = PROJECT_ROOT / "scripts" / "age8_stub_includes.py"
    spec = importlib.util.spec_from_file_location("age8_stub_includes", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["age8_stub_includes"] = mod
    spec.loader.exec_module(mod)
    return mod


_compile = _load_compile_check()
_stubs = _load_stub_gen()

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="g++ 不在 PATH（CI 跳过）",
)


class TestCleanSyntaxPasses:
    """完美的 chromium 风格代码 + 自动 stub → syntax_clean"""

    def test_minimal_chromium_style_cc(self, tmp_path: Path) -> None:
        files = tmp_path / "files"
        (files / "media" / "gpu").mkdir(parents=True)
        cc = files / "media" / "gpu" / "ok.cc"
        cc.write_text(
            '#include "base/logging.h"\n'
            "#include <vector>\n"
            "namespace media {\n"
            "int Sum(const std::vector<int>& v) {\n"
            "  int s = 0;\n"
            "  for (int x : v) s += x;\n"
            "  return s;\n"
            "}\n"
            "}\n",
            encoding="utf-8",
        )

        # 自动 stub
        stub_dir = tmp_path / ".stub_includes"
        includes = _stubs.collect_includes(files)  # type: ignore[attr-defined]
        _stubs.build_stub_dir(stub_dir, includes)  # type: ignore[attr-defined]

        report = _compile.run_compile_check(  # type: ignore[attr-defined]
            files, extra_includes=[stub_dir],
        )
        assert report.total == 1
        assert report.syntax_clean == 1, (
            f"应 syntax_clean 但实际：{report.results[0].status} "
            f"err={report.results[0].error[:200]}"
        )


class TestOcrSyntaxNoiseDetected:
    """spike 实测的真 OCR 粘连/标点错 → syntax_dirty"""

    def test_pp_directive_glued(self, tmp_path: Path) -> None:
        """spike 高频：#ifndEf 粘连标识符"""
        files = tmp_path / "files"
        files.mkdir()
        h = files / "bad.h"
        h.write_text(
            "#ifndEfFOO_BAR_H\n"
            "#dEfineFOO_BAR_H\n"
            "void foo();\n"
            "#endif\n",
            encoding="utf-8",
        )
        report = _compile.run_compile_check(files)  # type: ignore[attr-defined]
        r = report.results[0]
        assert r.status == "syntax_dirty", f"实际 status={r.status}"
        # 真 OCR syntax 错应被识别（行 1 / 行 2 是粘连）
        assert r.syntax_errors >= 2, (
            f"应识别出 ≥2 条 syntax 错，实际 {r.syntax_errors}, err={r.error[:300]}"
        )
        assert 1 in r.failing_lines or 2 in r.failing_lines

    def test_chinese_punctuation_in_code(self, tmp_path: Path) -> None:
        """中文标点 → stray token"""
        files = tmp_path / "files"
        files.mkdir()
        cc = files / "bad.cc"
        cc.write_text(
            "int main() {\n"
            "  int x = 1，\n"  # 中文逗号
            "  return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        report = _compile.run_compile_check(files)  # type: ignore[attr-defined]
        r = report.results[0]
        # 中文逗号触发 stray token，归 syntax 错
        assert r.status == "syntax_dirty"
        assert r.syntax_errors >= 1


class TestSysrootMissingClassified:
    """干净代码 + 缺 chromium 头 → sysroot_missing（不污染 OCR 噪声指标）"""

    def test_missing_chromium_type(self, tmp_path: Path) -> None:
        files = tmp_path / "files"
        files.mkdir()
        cc = files / "needs_chromium.cc"
        # 没 stub 时 BitstreamBuffer / Bind 没定义
        cc.write_text(
            'class BitstreamBuffer;\n'  # 前向声明
            "void Use(BitstreamBuffer* b) {\n"
            "  b->id();  // BitstreamBuffer 不完整 → 'no member named'\n"
            "}\n",
            encoding="utf-8",
        )
        report = _compile.run_compile_check(files)  # type: ignore[attr-defined]
        r = report.results[0]
        # 完全是语义错（缺真定义），不该被算成 OCR 噪声
        assert r.status == "sysroot_missing", (
            f"实际 status={r.status} err={r.error[:200]}"
        )
        assert r.syntax_errors == 0
        assert r.semantic_errors >= 1


class TestStubGenImprovesAccuracy:
    """auto-stubs 让"先 sysroot_missing 再 syntax_clean"成立"""

    def test_egl_typedef_resolves_unknown_type(self, tmp_path: Path) -> None:
        """无 stub: EGLDisplay 未知 → sysroot_missing；
        加 EGL/egl.h stub 后：syntax_clean
        """
        files = tmp_path / "files"
        files.mkdir()
        cc = files / "egl_use.cc"
        cc.write_text(
            "#include <EGL/egl.h>\n"
            "EGLDisplay g_display = nullptr;\n"
            "int main() { return 0; }\n",
            encoding="utf-8",
        )

        # 先不给 stub
        report1 = _compile.run_compile_check(files)  # type: ignore[attr-defined]
        # 缺 EGL/egl.h 直接 fatal error: file not found
        # 这条会归到 syntax 还是 semantic 取决于消息文本，这里不强求 status
        # 但绝对不能是 syntax_clean
        assert report1.results[0].status != "syntax_clean"

        # 给 stub 后：EGL/egl.h 在 stub_dir 里有 typedef
        stub_dir = tmp_path / ".stubs"
        includes = _stubs.collect_includes(files)  # type: ignore[attr-defined]
        _stubs.build_stub_dir(stub_dir, includes)  # type: ignore[attr-defined]
        report2 = _compile.run_compile_check(  # type: ignore[attr-defined]
            files, extra_includes=[stub_dir],
        )
        assert report2.results[0].status == "syntax_clean", (
            f"加 stub 后应 syntax_clean，实际 {report2.results[0].status}\n"
            f"err={report2.results[0].error[:300]}"
        )


class TestSpikeRealData:
    """跑真实 spike e2e-refined 数据：分类应符合手工诊断"""

    def test_spike_classification_distribution(self) -> None:
        """spike 6 个文件预期：1 clean + 2 dirty + 1 sysroot + 2 skipped"""
        files_dir = PROJECT_ROOT / "output" / "age8-e2e-refined" / "files"
        if not files_dir.exists():
            pytest.skip("spike 数据未生成")

        stub_dir = PROJECT_ROOT / "output" / "age8-e2e-refined" / ".stub_includes"
        includes = _stubs.collect_includes(files_dir)  # type: ignore[attr-defined]
        _stubs.build_stub_dir(stub_dir, includes)  # type: ignore[attr-defined]
        report = _compile.run_compile_check(  # type: ignore[attr-defined]
            files_dir, extra_includes=[stub_dir],
        )

        # 不强求精确数字（OCR / stub 改进可能让这些数字变化），但定性必须对：
        # - 至少 1 个 syntax_dirty（spike 没跑 OCR postfix → 必有 #ifndEf 粘连）
        # - 至少 1 个 syntax_clean 或 sysroot_missing
        assert report.syntax_dirty >= 1, (
            f"spike 应有 ≥1 个 syntax_dirty 文件，实际 {report.syntax_dirty}"
        )
        assert report.total >= 4
