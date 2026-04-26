#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""AGE-49 stub include 自动生成器。

g++ -fsyntax-only 跑 chromium 风格 #include 时，缺 chromium sysroot →
fatal error: 'media/foo.h' file not found。本脚本扫描 files/ 下源文件
的所有 #include，自动在 stub_dir 下创建对应的空 header（mkdir -p +
touch），并对常见类型族（EGL/、GLES/、OpenMAX）注入 typedef stub，
让 g++ 预处理 + 语义阶段不至于因缺类型直接 fatal。

用法（独立）：
    python scripts/age8_stub_includes.py \\
        --files-dir output/<task>/files/ \\
        --stub-dir  output/<task>/.stub_includes/

被 age8_compile_check.py 通过 --auto-stubs 选项调用。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# 匹配 #include "xxx" 或 #include <xxx>
_INCLUDE_RE = re.compile(
    r'^\s*#\s*include\s*[<"]([^>"]+)[>"]',
    re.MULTILINE,
)

# C/C++ 标准库头：g++ 自带，不需要 stub。命中则跳过。
_STDLIB_HEADERS = frozenset({
    "algorithm", "array", "atomic", "bitset", "cassert", "cctype",
    "cerrno", "cfloat", "chrono", "cinttypes", "climits", "cmath",
    "complex", "condition_variable", "cstddef", "cstdint", "cstdio",
    "cstdlib", "cstring", "ctime", "deque", "exception", "filesystem",
    "fstream", "functional", "future", "initializer_list", "iomanip",
    "iosfwd", "iostream", "iterator", "limits", "list", "locale", "map",
    "memory", "mutex", "new", "numeric", "optional", "ostream",
    "queue", "random", "ratio", "regex", "set", "shared_mutex",
    "sstream", "stack", "stdexcept", "streambuf", "string",
    "string_view", "system_error", "thread", "tuple", "type_traits",
    "typeindex", "typeinfo", "unordered_map", "unordered_set",
    "utility", "valarray", "variant", "vector",
    # 老式 C 头（也跳过）
    "assert.h", "ctype.h", "errno.h", "float.h", "inttypes.h",
    "limits.h", "math.h", "stdarg.h", "stdbool.h", "stddef.h",
    "stdint.h", "stdio.h", "stdlib.h", "string.h", "time.h",
    "unistd.h", "fcntl.h", "sys/types.h", "sys/stat.h", "sys/mman.h",
    "sys/socket.h", "sys/un.h", "sys/wait.h", "pthread.h", "dlfcn.h",
    "signal.h",
})

# 关键命名空间 → 注入的 typedef stub（让 g++ 看到该 #include 时获得基本类型）
# chromium / EGL / OpenMax 风格的强类型，没有这些会一片红。
_TYPEDEF_STUBS: dict[str, str] = {
    # OpenGL ES
    "GLES2/gl2.h": (
        "#pragma once\n"
        "typedef unsigned int GLuint;\n"
        "typedef int GLint;\n"
        "typedef unsigned int GLenum;\n"
        "typedef float GLfloat;\n"
        "typedef int GLsizei;\n"
        "typedef unsigned char GLboolean;\n"
        "typedef void GLvoid;\n"
    ),
    # EGL —— spike 高频
    "EGL/egl.h": (
        "#pragma once\n"
        "typedef void* EGLDisplay;\n"
        "typedef void* EGLContext;\n"
        "typedef void* EGLSurface;\n"
        "typedef void* EGLConfig;\n"
        "typedef int EGLint;\n"
        "typedef unsigned int EGLBoolean;\n"
        "typedef unsigned int EGLenum;\n"
        "typedef void* EGLNativeDisplayType;\n"
        "typedef void* EGLClientBuffer;\n"
    ),
    "EGL/eglext.h": (
        "#pragma once\n"
        "#include <EGL/egl.h>\n"
        "typedef void* EGLImageKHR;\n"
        "typedef void* EGLSyncKHR;\n"
    ),
    # X11
    "X11/Xlib.h": (
        "#pragma once\n"
        "typedef void* Display;\n"
        "typedef unsigned long Window;\n"
    ),
    # DRM
    "drm/drm_fourcc.h": (
        "#pragma once\n"
        "#define DRM_FORMAT_MOD_LINEAR 0\n"
        "typedef unsigned long uint64_t;\n"
    ),
    # chromium base —— spike 高频
    "base/logging.h": (
        "#pragma once\n"
        "#define DCHECK(x) ((void)0)\n"
        "#define DCHECK_EQ(a,b) ((void)0)\n"
        "#define DCHECK_LT(a,b) ((void)0)\n"
        "#define LOG(severity) (false ? (void)0 : (void)0)\n"
        "#define VLOG(level) (false ? (void)0 : (void)0)\n"
        "#define CHECK(x) ((void)0)\n"
        "#define CHECK_EQ(a,b) ((void)0)\n"
        "#define NOTREACHED() ((void)0)\n"
        "#define NOTIMPLEMENTED() ((void)0)\n"
    ),
    "base/bind.h": (
        "#pragma once\n"
        "namespace base {\n"
        "template<class F, class... Args>\n"
        "auto Bind(F&& f, Args&&... args) -> int { return 0; }\n"
        "template<class F, class... Args>\n"
        "auto BindOnce(F&& f, Args&&... args) -> int { return 0; }\n"
        "template<class F, class... Args>\n"
        "auto BindRepeating(F&& f, Args&&... args) -> int { return 0; }\n"
        "}  // namespace base\n"
    ),
    # chromium media —— OpenMax / VDA 高频
    "media/base/status.h": (
        "#pragma once\n"
        "namespace media {\n"
        "using StatusCodeType = int;\n"
        "using StatusGroupType = const char*;\n"
        "template<typename Traits> class TypedStatus {\n"
        " public:\n"
        "  TypedStatus() = default;\n"
        "  TypedStatus(typename Traits::Codes c) {}\n"
        "};\n"
        "}  // namespace media\n"
    ),
    "media/base/bitstream_buffer.h": (
        "#pragma once\n"
        "namespace media { class BitstreamBuffer { public: int id() const { return 0; } }; }\n"
    ),
}


def collect_includes(files_dir: Path) -> set[str]:
    """递归扫描 files_dir 下所有 .cc/.cpp/.cxx/.c/.h/.hpp/.hh 的 #include 路径"""
    code_exts = {".cc", ".cpp", ".cxx", ".c", ".h", ".hpp", ".hh"}
    includes: set[str] = set()
    for path in files_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in code_exts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _INCLUDE_RE.finditer(text):
            includes.add(m.group(1))
    return includes


def build_stub_dir(stub_dir: Path, includes: set[str]) -> dict[str, int]:
    """根据 #include 集合在 stub_dir 下落 stub header。

    - 标准库头：跳过（依赖 g++ 自带）
    - 命中 _TYPEDEF_STUBS：写带 typedef 的 stub
    - 其他：写空文件（仅 #pragma once，让预处理过得去）

    返回统计 ``{written, typedef_stubs, skipped_stdlib, total}``
    """
    stub_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "written": 0, "typedef_stubs": 0,
        "skipped_stdlib": 0, "total": len(includes),
    }
    for inc in sorted(includes):
        if inc in _STDLIB_HEADERS:
            stats["skipped_stdlib"] += 1
            continue
        target = stub_dir / inc
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _TYPEDEF_STUBS.get(inc)
        if content is not None:
            stats["typedef_stubs"] += 1
            target.write_text(content, encoding="utf-8")
        else:
            target.write_text(
                f"#pragma once\n// stub for {inc}\n", encoding="utf-8",
            )
        stats["written"] += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files-dir", type=Path, required=True)
    parser.add_argument("--stub-dir", type=Path, required=True)
    args = parser.parse_args()

    if not args.files_dir.is_dir():
        print(f"files-dir not found: {args.files_dir}")
        return 1
    includes = collect_includes(args.files_dir)
    stats = build_stub_dir(args.stub_dir, includes)
    print(
        f"stub_dir={args.stub_dir} "
        f"total={stats['total']} written={stats['written']} "
        f"typedef={stats['typedef_stubs']} stdlib_skipped={stats['skipped_stdlib']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
