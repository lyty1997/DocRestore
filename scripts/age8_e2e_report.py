#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""AGE-8 端到端验收报告：文件树 + 语法检查 + 失败样本。

跑完 code mode 任务后调用本脚本，对 ``output/<task>/`` 出一份：
1. 文件树（chromium 风格路径分组）
2. compile_check（auto-stubs）syntax_clean / syntax_dirty / sysroot_missing 分布
3. 真 OCR 语法噪声样本（前 N 行）

用法::

    python scripts/age8_e2e_report.py --task-dir output/chromium_vda_real
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _print_tree(files_dir: Path) -> None:
    """按目录分组打印文件清单"""
    if not files_dir.exists():
        print(f"  files/ 目录不存在: {files_dir}")
        return
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for f in sorted(files_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(files_dir)
            by_dir[str(rel.parent)].append(rel)
    print(f"  ── 文件树（{sum(len(v) for v in by_dir.values())} 个文件，{len(by_dir)} 个目录）──")
    for d in sorted(by_dir):
        print(f"  {d}/")
        for p in by_dir[d]:
            print(f"    └── {p.name}")


def _run_compile(task_dir: Path) -> dict[str, object]:
    files_dir = task_dir / "files"
    if not files_dir.exists():
        return {"error": "no files/ dir"}
    report_path = task_dir / "compile_report.json"
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "age8_compile_check.py"),
        "--files-dir", str(files_dir),
        "--auto-stubs",
        "--report", str(report_path),
    ]
    if (task_dir / "files-index.json").exists():
        cmd.extend(["--index", str(task_dir / "files-index.json")])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
    if report_path.exists():
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"error": "bad report"}
    return {"error": "no report generated"}


def _print_syntax_dirty_samples(report: dict[str, object], limit: int = 5) -> None:
    """对 syntax_dirty 的文件打印前 limit 条 OCR 真噪声 stderr 样本"""
    results = report.get("results")
    if not isinstance(results, list):
        return
    dirty = [r for r in results if isinstance(r, dict) and r.get("status") == "syntax_dirty"]
    if not dirty:
        print("  无 syntax_dirty 文件，OCR postfix + LLM refine 完全清理了真语法噪声 ✓")
        return
    print(f"  ── 真 OCR 语法噪声样本（前 {min(limit, len(dirty))} 条）──")
    for r in dirty[:limit]:
        path = r.get("path", "?")
        syn = r.get("syntax_errors", 0)
        sem = r.get("semantic_errors", 0)
        lines = r.get("failing_lines", [])
        err = r.get("error", "")
        print(f"  ▸ {path}  syntax={syn} sem={sem} lines={lines[:3]}")
        # 抽 stderr 第一条 error 行
        first_err_line = next(
            (line for line in str(err).split("\n") if "error:" in line),
            None,
        )
        if first_err_line:
            print(f"      {first_err_line.strip()[:120]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-dir", type=Path, required=True)
    args = parser.parse_args()

    task_dir = args.task_dir
    if not task_dir.is_dir():
        print(f"task-dir 不存在: {task_dir}", file=sys.stderr)
        return 1

    print(f"\n=== AGE-8 端到端验收报告: {task_dir.name} ===\n")

    # 1. files-index.json + 文件树
    index_path = task_dir / "files-index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(index, list):
            langs: dict[str, int] = defaultdict(int)
            for e in index:
                if isinstance(e, dict):
                    langs[str(e.get("language", "?"))] += 1
            print(
                f"  files-index.json: {len(index)} entries  "
                f"languages={dict(langs)}",
            )
    else:
        print("  files-index.json 缺失（代码模式 pipeline 未跑通？）")

    print()
    _print_tree(task_dir / "files")

    # 2. compile_check
    print("\n  ── 语法检查（g++ -fsyntax-only + auto stubs）──")
    report = _run_compile(task_dir)
    if "error" in report:
        print(f"  {report['error']}")
        return 1

    # 3. 真 OCR 噪声样本
    print()
    _print_syntax_dirty_samples(report)

    # 4. Summary
    total = report.get("total", 0)
    clean = report.get("syntax_clean", 0)
    dirty = report.get("syntax_dirty", 0)
    sysroot = report.get("sysroot_missing", 0)
    skipped = report.get("skipped", 0)
    print("\n  ── 结论 ──")
    if isinstance(total, int) and total > 0:
        ratio = (
            f"{100 * (int(clean) if isinstance(clean, int) else 0) / total:.1f}%"
        )
        print(f"  syntax_clean / total = {clean}/{total} = {ratio}")
        print(f"  syntax_dirty (真 OCR 噪声) = {dirty}")
        print(f"  sysroot_missing (缺 chromium 头) = {sysroot}")
        print(f"  skipped (无工具/无映射) = {skipped}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
