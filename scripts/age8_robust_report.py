#!/usr/bin/env python3
# Copyright 2026 @lyty1997

# mypy: ignore-errors
"""AGE-8 多数据集鲁棒性聚合报告

读 output/age8-robust/<dataset>/per_image.jsonl，按数据集汇总：
  - 总图数 / 检出 anchor 数 / 检出率
  - mono 分布 / no_anchor 数
  - 列长 / above / below / sidebar 平均
  - assembly 集成统计

用法：
    python scripts/age8_robust_report.py
        [--root output/age8-robust]
        [--out output/age8-robust/REPORT.md]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _stat(items: list[dict]) -> dict:
    if not items:
        return {}
    n = len(items)
    n_anchor = Counter(it.get("anchor_count", 0) for it in items)
    success = sum(1 for it in items if it.get("anchor_count", 0) >= 1)
    monos = [it["max_monotonic"] for it in items if it.get("max_monotonic") is not None]

    flag_count = Counter()
    asm_flag_count = Counter()
    for it in items:
        for f in it.get("flags", []):
            flag_count[f] += 1
        for f in it.get("assembly_flags", []):
            asm_flag_count[f] += 1

    col_lengths = [
        c for it in items for c in it.get("assembled_lines_per_col", [])
    ]
    char_widths = [
        c for it in items for c in it.get("char_widths", [])
    ]
    line_heights = [
        h for it in items for h in it.get("line_heights", [])
    ]
    line_gaps_total = sum(it.get("total_line_gaps", 0) for it in items)

    def _stats_summary(vals):
        if not vals:
            return None
        return {
            "n": len(vals),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "median": round(statistics.median(vals), 2),
            "mean": round(statistics.mean(vals), 2),
        }

    return {
        "total": n,
        "success": success,
        "success_rate": round(success / n, 4),
        "anchor_count_distribution": dict(sorted(n_anchor.items())),
        "max_monotonic_avg": round(statistics.mean(monos), 4) if monos else None,
        "max_monotonic_geq_0.9": (
            sum(1 for v in monos if v >= 0.9) if monos else 0
        ),
        "code.no_anchor": flag_count.get("code.no_anchor", 0),
        "code.weak_monotonic": flag_count.get("code.weak_monotonic", 0),
        "code.single_anchor": flag_count.get("code.single_anchor", 0),
        "code.three_plus_anchors": flag_count.get("code.three_plus_anchors", 0),
        "all_flags": dict(flag_count),
        "all_assembly_flags": dict(asm_flag_count),
        "lines_per_col_stats": _stats_summary(col_lengths),
        "char_width_stats": _stats_summary(char_widths),
        "line_height_stats": _stats_summary(line_heights),
        "total_line_gaps": line_gaps_total,
    }


def _format_md(report: dict[str, dict]) -> str:
    out: list[str] = []
    out.append("# AGE-8 行号列锚点方案多数据集鲁棒性报告\n")
    out.append("## 总览\n")
    out.append(
        "| dataset | total | success | rate | mono≥0.9 | no_anchor | weak | "
        "single | 3+ |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|")
    for ds, st in report.items():
        out.append(
            f"| {ds} | {st['total']} | {st['success']} | "
            f"{st['success_rate']:.2%} | {st['max_monotonic_geq_0.9']} | "
            f"{st['code.no_anchor']} | {st['code.weak_monotonic']} | "
            f"{st['code.single_anchor']} | {st['code.three_plus_anchors']} |"
        )

    for ds, st in report.items():
        out.append(f"\n## {ds}\n")
        out.append(f"- total: **{st['total']}**")
        out.append(
            f"- success rate: **{st['success_rate']:.2%}** "
            f"({st['success']}/{st['total']})"
        )
        out.append(
            f"- anchor_count_distribution: `{st['anchor_count_distribution']}`"
        )
        out.append(f"- mono ≥ 0.9 count: {st['max_monotonic_geq_0.9']}")
        out.append(f"- mean max_monotonic: {st['max_monotonic_avg']}")
        if st["lines_per_col_stats"]:
            s = st["lines_per_col_stats"]
            out.append(
                f"- 每栏代码行数：min={s['min']} max={s['max']} "
                f"median={s['median']} mean={s['mean']}"
            )
        if st["char_width_stats"]:
            s = st["char_width_stats"]
            out.append(
                f"- char_width(px)：min={s['min']} max={s['max']} "
                f"median={s['median']}"
            )
        if st["line_height_stats"]:
            s = st["line_height_stats"]
            out.append(
                f"- line_height(px)：min={s['min']} max={s['max']} "
                f"median={s['median']}"
            )
        out.append(f"- total_line_gaps: {st['total_line_gaps']}")
        if st["all_flags"]:
            out.append(f"- 所有 layout flag 分布：`{st['all_flags']}`")
        if st["all_assembly_flags"]:
            out.append(f"- 所有 assembly flag 分布：`{st['all_assembly_flags']}`")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=PROJECT_ROOT / "output" / "age8-robust",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    args.out = args.out or (args.root / "REPORT.md")

    if not args.root.exists():
        print(f"no root: {args.root}")
        return 1

    report: dict[str, dict] = {}
    for sub in sorted(args.root.iterdir()):
        per_img = sub / "per_image.jsonl"
        if not per_img.exists():
            continue
        items = [json.loads(l) for l in per_img.read_text().splitlines() if l.strip()]
        report[sub.name] = _stat(items)
        print(f"{sub.name}: {len(items)} images")

    md = _format_md(report)
    args.out.write_text(md, encoding="utf-8")
    print(f"\n→ {args.out}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
