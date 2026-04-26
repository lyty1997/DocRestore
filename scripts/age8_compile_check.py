#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

# mypy: ignore-errors

"""AGE-8 Phase 3.2：编译验证脚本

对 ``output/<task>/files/`` 下还原出的每个源文件跑语法检查，统计通过率
+ 错误细节，回填 ``files-index.json`` 的 ``compile_status`` 字段供前端
（AGE-50）展示。

按语言分发：
  - ``.cc/.cpp/.cxx/.h/.hpp/.hh`` → ``g++ -fsyntax-only -std=c++17 -x c++``
  - ``.c`` → ``gcc -fsyntax-only``
  - ``.py`` → ``python3 -m py_compile``
  - ``.gn/.gni`` → ``gn format --stdin < <file>``（缺 gn 工具 → skip）
  - ``.js/.ts/.tsx`` → ``node --check`` / ``tsc --noEmit``（缺则 skip）
  - 其他后缀 → skip

工具缺失（``which`` 找不到）→ 标 ``compile.tool_unavailable``，不算
失败也不算通过。

用法：
    python scripts/age8_compile_check.py \\
        --files-dir output/<task>/files/ \\
        --index    output/<task>/files-index.json \\
        --report   output/<task>/compile_report.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# C/C++ 编译命令：-w 关警告、-fpermissive 让"未定义类型"等语义错降级 → 让纯
# **语法**层面的错误（粘连 / 未匹配括号 / 漏分号 / 中文标点等）能被检出，
# 同时不让 chromium 缺 sysroot 的语义错把"代码模式还原能力"指标污染。
# stub include 通过 -I 注入（main 调用 build_stub_includes 后传入）。
_CXX_BASE = ["-fsyntax-only", "-std=c++17", "-w", "-fpermissive"]
_C_BASE = ["-fsyntax-only", "-w"]

# 后缀 → (描述, 命令模板，None=skip)
# 命令模板用 "{file}" 占位；"{stub_dir}" 占位会被 main 替换为实际 -I 列表
_LANG_COMMANDS: dict[str, tuple[str, list[str] | None]] = {
    ".cc":   ("c++", ["g++", *_CXX_BASE, "-x", "c++", "{file}"]),
    ".cpp":  ("c++", ["g++", *_CXX_BASE, "-x", "c++", "{file}"]),
    ".cxx":  ("c++", ["g++", *_CXX_BASE, "-x", "c++", "{file}"]),
    ".hh":   ("c++", ["g++", *_CXX_BASE, "-x", "c++-header", "{file}"]),
    ".hpp":  ("c++", ["g++", *_CXX_BASE, "-x", "c++-header", "{file}"]),
    ".h":    ("c++", ["g++", *_CXX_BASE, "-x", "c++-header", "{file}"]),
    ".c":    ("c",   ["gcc", *_C_BASE, "{file}"]),
    ".py":   ("python", [sys.executable, "-m", "py_compile", "{file}"]),
    ".gn":   ("gn",  ["gn", "format", "--dry-run", "{file}"]),
    ".gni":  ("gn",  ["gn", "format", "--dry-run", "{file}"]),
    ".js":   ("javascript", ["node", "--check", "{file}"]),
    ".mjs":  ("javascript", ["node", "--check", "{file}"]),
    ".ts":   ("typescript", ["tsc", "--noEmit", "{file}"]),
    ".tsx":  ("typescript", ["tsc", "--noEmit", "{file}"]),
}

# g++ / clang 错误行号正则：``file:LINE:COL: error: msg``
_ERROR_LINE_RE = re.compile(
    r":(\d+):(\d+):\s*(?:fatal\s+)?error:\s*(.*)",
    re.IGNORECASE,
)

# **真 OCR 语法噪声**关键词（粘连 / 中文标点 / 未匹配 / 杂散 token 等）。
# 这些是代码模式还原能力的指标 —— 期望 0。
_SYNTAX_ERROR_PATTERNS = [
    # 强 OCR 噪声信号：粘连 / 全角标点 / token 不匹配
    "invalid preprocessing directive",  # #ifndEfOMX_GPU 粘连标志
    "stray ",                            # stray '\343' (中文标点未转 ascii)
    "unterminated",                      # 引号 / 块注释未闭
    "missing terminating",               # 引号未闭
    "extra ';'",
    "extra qualification",
    # "expected X" 类常常是上面错误的级联，不当作首要 OCR 噪声指标 ——
    # 当文件本身有强信号时这些是连带；当只有这些时多半是 sysroot 缺类型
    # 导致解析失败（不是 OCR 责任）。
]

# **缺 chromium sysroot 的语义错**（未定义类型 / 未知 namespace 等）。
# 不算 OCR 噪声 —— chromium 的 base::Bind / BitstreamBuffer 等本来就需要
# 全套头文件才能解析。这类不影响代码模式还原能力评估。
_SEMANTIC_ERROR_PATTERNS = [
    "has not been declared",
    "was not declared",
    "no member named",
    "no type named",
    "not a class or namespace",
    "use of undeclared identifier",
    "unknown type name",
    "incomplete type",
    "use of class template",
    "does not name a type",
    "template argument",
    "in nested-name-specifier",  # `Foo::bar` 里 Foo 没定义时 g++ 也这么报
    "expected nested-name-specifier",
    "is not a type",
    "redefinition of",  # 同一类型多 stub 时容易触发
]


def _classify_errors(err_text: str) -> tuple[int, int, list[int]]:
    """按 stderr 内容区分真 OCR 语法错 vs 缺 sysroot 语义错。

    Returns
    -------
    (syntax_errors, semantic_errors, syntax_lines)
        ``syntax_lines`` 仅含真 OCR 语法错的行号（用于前端高亮 OCR 噪声）。
    """
    syntax = 0
    semantic = 0
    syntax_lines: set[int] = set()
    for m in _ERROR_LINE_RE.finditer(err_text):
        line_no = int(m.group(1))
        # group(1)=line, group(2)=col, group(3)=msg
        msg = m.group(3).lower()
        if any(p in msg for p in _SYNTAX_ERROR_PATTERNS):
            syntax += 1
            syntax_lines.add(line_no)
        elif any(p in msg for p in _SEMANTIC_ERROR_PATTERNS):
            semantic += 1
        else:
            # 未归类的视为 semantic cascade（缺类型 → "expected X" 等连锁错），
            # 真 OCR 噪声有 _SYNTAX_ERROR_PATTERNS 强信号兜底，不会漏
            semantic += 1
    return syntax, semantic, sorted(syntax_lines)


@dataclass
class CompileResult:
    path: str
    language: str
    status: str               # syntax_clean / syntax_dirty / sysroot_missing / failed / skipped
    duration_ms: int = 0
    error: str = ""           # 截短的 stderr（< 4KB）
    failing_lines: list[int] = field(default_factory=list)  # 真 OCR 语法错行号
    syntax_errors: int = 0    # 粘连 / 标点 / 未匹配括号 等（OCR 噪声）
    semantic_errors: int = 0  # 未定义类型 / 缺 namespace（缺 chromium 全量头）
    skip_reason: str = ""     # 为什么 skipped（无工具/无映射）


@dataclass
class CompileReport:
    total: int
    syntax_clean: int      # g++ 通过 + 无任何错误
    syntax_dirty: int      # 有真 OCR 语法错（粘连/标点/未匹配）
    sysroot_missing: int   # 仅语义错（缺 chromium 全量头），语法本身干净
    skipped: int
    results: list[CompileResult]
    tool_availability: dict[str, bool]

    # 兼容旧字段：passed 等价 syntax_clean，failed 等价 syntax_dirty
    @property
    def passed(self) -> int:
        return self.syntax_clean

    @property
    def failed(self) -> int:
        return self.syntax_dirty


def _check_tool_available(cmd_template: list[str]) -> bool:
    """命令的第一个 token 是否可执行"""
    return shutil.which(cmd_template[0]) is not None


def _run_one_file(
    file_path: Path,
    *,
    extra_includes: list[Path] | None = None,
) -> CompileResult:
    ext = file_path.suffix.lower()
    if ext not in _LANG_COMMANDS:
        return CompileResult(
            path=str(file_path), language="?",
            status="skipped",
            skip_reason=f"unsupported extension {ext}",
        )
    lang, template = _LANG_COMMANDS[ext]
    if template is None or not _check_tool_available(template):
        return CompileResult(
            path=str(file_path), language=lang,
            status="skipped",
            skip_reason=f"tool {template[0] if template else '?'} unavailable",
        )

    cmd = [arg.replace("{file}", str(file_path)) for arg in template]
    # 在 g++ / gcc 命令前面插入 -I 选项（保留 -fsyntax-only 等参数顺序）
    if extra_includes and template[0] in {"g++", "gcc"}:
        # 找到 "{file}" 替换后的真实路径位置，把 -I 插在它之前
        file_idx = cmd.index(str(file_path))
        for inc in reversed(extra_includes):
            cmd.insert(file_idx, str(inc))
            cmd.insert(file_idx, "-I")
    t_start = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=30, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return CompileResult(
            path=str(file_path), language=lang,
            status="failed",
            error=f"subprocess error: {exc}",
            duration_ms=int((time.time() - t_start) * 1000),
        )
    duration_ms = int((time.time() - t_start) * 1000)

    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()
    err_text = stderr or stdout
    syntax_n, semantic_n, syntax_lines = _classify_errors(err_text)

    if proc.returncode == 0:
        return CompileResult(
            path=str(file_path), language=lang,
            status="syntax_clean", duration_ms=duration_ms,
        )
    # 失败但只有语义错（缺 chromium sysroot）→ syntax 仍是干净的
    if syntax_n == 0 and semantic_n > 0:
        return CompileResult(
            path=str(file_path), language=lang,
            status="sysroot_missing", duration_ms=duration_ms,
            error=err_text[:4000],
            syntax_errors=syntax_n, semantic_errors=semantic_n,
        )
    return CompileResult(
        path=str(file_path), language=lang,
        status="syntax_dirty", duration_ms=duration_ms,
        error=err_text[:4000],
        failing_lines=syntax_lines,
        syntax_errors=syntax_n, semantic_errors=semantic_n,
    )


def run_compile_check(
    files_dir: Path,
    *,
    relative_paths: list[str] | None = None,
    extra_includes: list[Path] | None = None,
) -> CompileReport:
    """对 files_dir 下所有支持的源文件跑语法检查

    relative_paths 非空时仅检查指定列表（来自 files-index.json）；否则递归
    扫描整个目录。``extra_includes`` 是额外的 -I 目录列表（chromium stub
    headers 等），会被注入 g++/gcc 命令。
    """
    # files_dir 自身也加进 -I：chromium 风格 #include "media/foo.h" 在 files/
    # 下能 self-resolve（文件树就是按 chromium 路径组织的）
    final_includes = [files_dir]
    if extra_includes:
        final_includes.extend(extra_includes)

    if relative_paths is not None:
        targets = [files_dir / rel for rel in relative_paths]
    else:
        targets = sorted(
            p for p in files_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in _LANG_COMMANDS
        )

    results: list[CompileResult] = []
    for f in targets:
        if not f.exists():
            results.append(CompileResult(
                path=str(f.relative_to(files_dir)),
                language="?",
                status="skipped",
                skip_reason="file not found",
            ))
            continue
        r = _run_one_file(f, extra_includes=final_includes)
        # 保存相对路径而非绝对路径
        try:
            r.path = str(f.relative_to(files_dir))
        except ValueError:
            r.path = str(f)
        results.append(r)

    tool_avail: dict[str, bool] = {}
    seen: set[str] = set()
    for _lang, tmpl in _LANG_COMMANDS.values():
        if tmpl is None:
            continue
        tool = tmpl[0]
        if tool in seen:
            continue
        seen.add(tool)
        tool_avail[tool] = _check_tool_available(tmpl)

    return CompileReport(
        total=len(results),
        syntax_clean=sum(1 for r in results if r.status == "syntax_clean"),
        syntax_dirty=sum(1 for r in results if r.status == "syntax_dirty"),
        sysroot_missing=sum(
            1 for r in results if r.status == "sysroot_missing"
        ),
        skipped=sum(1 for r in results if r.status == "skipped"),
        results=results,
        tool_availability=tool_avail,
    )


def update_index_with_compile(
    index_path: Path, report: CompileReport,
) -> None:
    """回填 files-index.json：每条 entry 加 compile_status / compile_error"""
    if not index_path.exists():
        return
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(index, list):
        return
    by_path = {r.path: r for r in report.results}
    for entry in index:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path")
        if not isinstance(rel, str):
            continue
        result = by_path.get(rel)
        if not result:
            continue
        entry["compile_status"] = result.status
        if result.status in ("syntax_dirty", "sysroot_missing"):
            entry["compile_error"] = result.error[:1000]
            entry["compile_failing_lines"] = result.failing_lines
            entry["compile_syntax_errors"] = result.syntax_errors
            entry["compile_semantic_errors"] = result.semantic_errors
        elif result.status == "skipped":
            entry["compile_skip_reason"] = result.skip_reason
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files-dir", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--auto-stubs", action="store_true",
        help="自动扫 #include 并生成 stub headers（推荐 chromium 这类缺 sysroot 场景）",
    )
    parser.add_argument(
        "--stub-dir", type=Path, default=None,
        help="stub headers 目录；与 --auto-stubs 配合使用，未指定时落到 files-dir 同级 .stub_includes/",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.files_dir.exists():
        print(f"files-dir not found: {args.files_dir}", file=sys.stderr)
        return 1

    relative_paths: list[str] | None = None
    if args.index and args.index.exists():
        idx = json.loads(args.index.read_text(encoding="utf-8"))
        if isinstance(idx, list):
            relative_paths = [
                e["path"] for e in idx
                if isinstance(e, dict) and isinstance(e.get("path"), str)
            ]

    extra_includes: list[Path] = []
    if args.auto_stubs:
        # 局部 import 避免脚本 standalone 时多绕一圈
        from age8_stub_includes import build_stub_dir, collect_includes

        stub_dir = args.stub_dir or args.files_dir.parent / ".stub_includes"
        includes = collect_includes(args.files_dir)
        stats = build_stub_dir(stub_dir, includes)
        print(
            f"stub headers: total={stats['total']} written={stats['written']} "
            f"typedef={stats['typedef_stubs']} skipped_stdlib={stats['skipped_stdlib']}",
        )
        extra_includes.append(stub_dir)

    report = run_compile_check(
        args.files_dir,
        relative_paths=relative_paths,
        extra_includes=extra_includes,
    )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "total": report.total,
                    "syntax_clean": report.syntax_clean,
                    "syntax_dirty": report.syntax_dirty,
                    "sysroot_missing": report.sysroot_missing,
                    "skipped": report.skipped,
                    "tool_availability": report.tool_availability,
                    "results": [asdict(r) for r in report.results],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    if args.index:
        update_index_with_compile(args.index, report)

    print(
        f"total={report.total} "
        f"syntax_clean={report.syntax_clean} "
        f"syntax_dirty={report.syntax_dirty} "
        f"sysroot_missing={report.sysroot_missing} "
        f"skipped={report.skipped}",
    )
    for r in report.results:
        if r.status == "syntax_dirty":
            print(
                f"  DIRTY {r.path} ({r.language}, {r.duration_ms}ms)  "
                f"syntax={r.syntax_errors} sem={r.semantic_errors}  "
                f"lines={r.failing_lines[:3]}",
            )
        elif r.status == "sysroot_missing":
            print(
                f"  SYSROOT {r.path} ({r.language}, {r.duration_ms}ms)  "
                f"sem={r.semantic_errors}",
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
