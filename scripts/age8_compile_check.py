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

# 后缀 → (描述, 命令模板，None=skip)
# 命令模板用 "{file}" 占位
_LANG_COMMANDS: dict[str, tuple[str, list[str] | None]] = {
    ".cc":   ("c++", ["g++", "-fsyntax-only", "-std=c++17", "-x", "c++", "{file}"]),
    ".cpp":  ("c++", ["g++", "-fsyntax-only", "-std=c++17", "-x", "c++", "{file}"]),
    ".cxx":  ("c++", ["g++", "-fsyntax-only", "-std=c++17", "-x", "c++", "{file}"]),
    ".hh":   ("c++", ["g++", "-fsyntax-only", "-std=c++17", "-x", "c++-header", "{file}"]),
    ".hpp":  ("c++", ["g++", "-fsyntax-only", "-std=c++17", "-x", "c++-header", "{file}"]),
    ".h":    ("c++", ["g++", "-fsyntax-only", "-std=c++17", "-x", "c++-header", "{file}"]),
    ".c":    ("c",   ["gcc", "-fsyntax-only", "{file}"]),
    ".py":   ("python", [sys.executable, "-m", "py_compile", "{file}"]),
    ".gn":   ("gn",  ["gn", "format", "--dry-run", "{file}"]),
    ".gni":  ("gn",  ["gn", "format", "--dry-run", "{file}"]),
    ".js":   ("javascript", ["node", "--check", "{file}"]),
    ".mjs":  ("javascript", ["node", "--check", "{file}"]),
    ".ts":   ("typescript", ["tsc", "--noEmit", "{file}"]),
    ".tsx":  ("typescript", ["tsc", "--noEmit", "{file}"]),
}

# g++ / clang 错误行号正则：``file:LINE:COL: error: msg``
_ERROR_LINE_RE = re.compile(r":(\d+):(\d+): (?:fatal\s+)?error:", re.IGNORECASE)


@dataclass
class CompileResult:
    path: str
    language: str
    status: str               # passed / failed / skipped
    duration_ms: int = 0
    error: str = ""           # 截短的 stderr（< 4KB）
    failing_lines: list[int] = field(default_factory=list)
    skip_reason: str = ""     # 为什么 skipped（无工具/无映射）


@dataclass
class CompileReport:
    total: int
    passed: int
    failed: int
    skipped: int
    results: list[CompileResult]
    tool_availability: dict[str, bool]


def _check_tool_available(cmd_template: list[str]) -> bool:
    """命令的第一个 token 是否可执行"""
    return shutil.which(cmd_template[0]) is not None


def _run_one_file(file_path: Path) -> CompileResult:
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
    failing_lines = sorted({
        int(m.group(1)) for m in _ERROR_LINE_RE.finditer(err_text)
    })

    if proc.returncode == 0:
        return CompileResult(
            path=str(file_path), language=lang,
            status="passed", duration_ms=duration_ms,
        )
    return CompileResult(
        path=str(file_path), language=lang,
        status="failed", duration_ms=duration_ms,
        error=err_text[:4000],
        failing_lines=failing_lines,
    )


def run_compile_check(
    files_dir: Path,
    *,
    relative_paths: list[str] | None = None,
) -> CompileReport:
    """对 files_dir 下所有支持的源文件跑语法检查

    relative_paths 非空时仅检查指定列表（来自 files-index.json）；否则递归
    扫描整个目录。
    """
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
        r = _run_one_file(f)
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
        passed=sum(1 for r in results if r.status == "passed"),
        failed=sum(1 for r in results if r.status == "failed"),
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
        if result.status == "failed":
            entry["compile_error"] = result.error[:1000]
            entry["compile_failing_lines"] = result.failing_lines
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

    report = run_compile_check(
        args.files_dir, relative_paths=relative_paths,
    )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "total": report.total,
                    "passed": report.passed,
                    "failed": report.failed,
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
        f"total={report.total} passed={report.passed} "
        f"failed={report.failed} skipped={report.skipped}",
    )
    for r in report.results:
        if r.status == "failed":
            print(
                f"  FAIL {r.path} ({r.language}, {r.duration_ms}ms)  "
                f"first_lines={r.failing_lines[:3]}",
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
