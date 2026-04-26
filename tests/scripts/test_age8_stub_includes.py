# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""AGE-49 stub include 自动生成器测试。

确认：
- collect_includes 扫描所有 .h/.cc/.cpp 的 #include "xxx" 和 <xxx>
- build_stub_dir 跳过标准库头、给关键类型注入 typedef、其他写空 stub
- 行为对 chromium 风格 #include "media/foo.h" 友好
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from age8_stub_includes import (  # type: ignore[import-not-found]
    _STDLIB_HEADERS,
    _TYPEDEF_STUBS,
    build_stub_dir,
    collect_includes,
)


@pytest.fixture
def files_root(tmp_path: Path) -> Path:
    """构造一个迷你 files/ 树含多种 #include 形态。"""
    root = tmp_path / "files"
    (root / "media" / "gpu" / "openmax").mkdir(parents=True)
    (root / "base").mkdir(parents=True)

    (root / "media" / "gpu" / "openmax" / "foo.cc").write_text(
        '#include "base/logging.h"\n'
        '#include "media/gpu/openmax/foo.h"\n'
        "#include <map>\n"
        "#include <EGL/egl.h>\n"
        "int main() { return 0; }\n",
        encoding="utf-8",
    )
    (root / "media" / "gpu" / "openmax" / "foo.h").write_text(
        '#include "base/bind.h"\n'
        "#include <vector>\n",
        encoding="utf-8",
    )
    (root / "base" / "stub.cc").write_text(
        '#  include   "base/logging.h"\n'  # 多空格变体
        '#include"no_space.h"\n',
        encoding="utf-8",
    )
    return root


class TestCollectIncludes:
    def test_finds_quoted_and_angle(self, files_root: Path) -> None:
        result = collect_includes(files_root)
        assert "base/logging.h" in result
        assert "base/bind.h" in result
        assert "media/gpu/openmax/foo.h" in result
        assert "map" in result
        assert "vector" in result
        assert "EGL/egl.h" in result
        assert "no_space.h" in result

    def test_handles_whitespace_variations(self, files_root: Path) -> None:
        result = collect_includes(files_root)
        # 多空格的 #  include  "..." 也应被识别
        assert "base/logging.h" in result
        assert "no_space.h" in result

    def test_empty_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert collect_includes(empty) == set()

    def test_skips_non_code_files(self, tmp_path: Path) -> None:
        """README.md 里的 #include 不该被扫到（避免文档串味）"""
        d = tmp_path / "f"
        d.mkdir()
        (d / "README.md").write_text(
            '示例：`#include "fake.h"`\n', encoding="utf-8",
        )
        assert collect_includes(d) == set()


class TestBuildStubDir:
    def test_skips_stdlib(self, tmp_path: Path) -> None:
        stub = tmp_path / "stub"
        stats = build_stub_dir(stub, {"map", "vector", "base/foo.h"})
        assert stats["skipped_stdlib"] == 2
        assert stats["written"] == 1
        assert (stub / "base" / "foo.h").exists()
        assert not (stub / "map").exists()
        assert not (stub / "vector").exists()

    def test_typedef_stubs_have_content(self, tmp_path: Path) -> None:
        """关键类型族（EGL/）落实际 typedef 而非空 stub"""
        stub = tmp_path / "stub"
        build_stub_dir(stub, {"EGL/egl.h"})
        text = (stub / "EGL" / "egl.h").read_text(encoding="utf-8")
        assert "EGLDisplay" in text
        assert "typedef" in text

    def test_unknown_include_gets_empty_stub(self, tmp_path: Path) -> None:
        stub = tmp_path / "stub"
        build_stub_dir(stub, {"random/proprietary.h"})
        text = (stub / "random" / "proprietary.h").read_text()
        assert "#pragma once" in text
        # 无 typedef 内容
        assert "typedef" not in text

    def test_creates_nested_dirs(self, tmp_path: Path) -> None:
        stub = tmp_path / "stub"
        build_stub_dir(stub, {"a/b/c/d.h"})
        assert (stub / "a" / "b" / "c" / "d.h").is_file()

    def test_stats_consistency(self, tmp_path: Path) -> None:
        stub = tmp_path / "stub"
        includes = {"map", "EGL/egl.h", "base/foo.h", "vector"}
        stats = build_stub_dir(stub, includes)
        assert stats["total"] == len(includes)
        assert (
            stats["written"] + stats["skipped_stdlib"]
            == stats["total"]
        )
        assert stats["typedef_stubs"] == 1  # 只有 EGL/egl.h


class TestStdlibCoverage:
    """_STDLIB_HEADERS 覆盖常用 C++17 头，避免不必要的 stub 污染"""

    def test_common_cpp_headers(self) -> None:
        for h in ("vector", "string", "memory", "map", "iostream", "thread"):
            assert h in _STDLIB_HEADERS, f"{h} 应在标准库白名单"

    def test_common_c_headers(self) -> None:
        for h in ("stdio.h", "stdlib.h", "string.h", "stdint.h"):
            assert h in _STDLIB_HEADERS, f"{h} 应在标准库白名单"


class TestTypedefStubsContent:
    """关键 typedef stub 包含必要类型，避免 chromium 缺这些就编不过"""

    def test_egl_egl_h_has_core_types(self) -> None:
        content = _TYPEDEF_STUBS["EGL/egl.h"]
        for t in ("EGLDisplay", "EGLContext", "EGLSurface", "EGLConfig", "EGLint"):
            assert t in content, f"{t} 应在 EGL/egl.h stub"

    def test_eglext_includes_egl(self) -> None:
        """EGL/eglext.h 应该 #include 基础 EGL/egl.h"""
        content = _TYPEDEF_STUBS["EGL/eglext.h"]
        assert "EGL/egl.h" in content
        assert "EGLImageKHR" in content
