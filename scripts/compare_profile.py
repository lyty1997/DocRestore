#!/usr/bin/env python3
# Copyright 2026 @lyty1997
# Licensed under the Apache License, Version 2.0

"""对比两份 profile.json，打印关键指标变化。

用法:
    python scripts/compare_profile.py BASELINE.json NEW.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_events(path: Path) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return raw


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合关键指标。"""
    by_name: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0.0, "total": 0.0, "max": 0.0},
    )
    for ev in events:
        b = by_name[ev["name"]]
        b["count"] += 1
        b["total"] += ev["duration_s"]
        b["max"] = max(b["max"], ev["duration_s"])

    total_events = [e for e in events if e["name"] == "pipeline.total"]
    wall = total_events[0]["duration_s"] if total_events else 0.0

    # final_refine 单次耗时分布
    finals = [e for e in events if e["name"] == "llm.final_refine"]
    # api_call 输入长度 vs 耗时
    calls = [e for e in events if e["name"] == "llm.api_call"]
    sem_waits = [e["duration_s"] for e in events if e["name"] == "llm.sem_wait"]

    return {
        "wall": wall,
        "by_name": dict(by_name),
        "finals": [(e["duration_s"], e.get("attrs", {})) for e in finals],
        "calls": [(e["duration_s"], e.get("attrs", {})) for e in calls],
        "sem_waits": sorted(sem_waits),
    }


def pct(new: float, base: float) -> str:
    if base == 0:
        return "n/a"
    delta = (new - base) / base * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    base = summarize(load_events(Path(sys.argv[1])))
    new = summarize(load_events(Path(sys.argv[2])))

    pct_wall = pct(new["wall"], base["wall"])
    print(
        f"wall time: {base['wall']:.2f}s → "
        f"{new['wall']:.2f}s ({pct_wall})",
    )
    print()

    # 按 name 聚合对比
    names = sorted(
        set(base["by_name"]) | set(new["by_name"]),
        key=lambda k: -(new["by_name"].get(k, {}).get("total", 0.0)),
    )
    print(f"{'stage':<25} {'base.total':>12} {'new.total':>12} {'Δ%':>8} "
          f"{'base.count':>10} {'new.count':>10}")
    for n in names:
        b = base["by_name"].get(n, {"count": 0, "total": 0.0})
        v = new["by_name"].get(n, {"count": 0, "total": 0.0})
        if b["total"] + v["total"] < 0.5:  # 跳过极小项
            continue
        print(f"{n:<25} {b['total']:>12.2f} {v['total']:>12.2f} "
              f"{pct(v['total'], b['total']):>8} "
              f"{b['count']:>10d} {v['count']:>10d}")

    print("\n-- final_refine 分布 --")
    print(f"  base: {len(base['finals'])} 次, "
          f"总 {sum(d for d, _ in base['finals']):.1f}s, "
          f"最大 {max((d for d, _ in base['finals']), default=0):.1f}s")
    print(f"  new:  {len(new['finals'])} 次, "
          f"总 {sum(d for d, _ in new['finals']):.1f}s, "
          f"最大 {max((d for d, _ in new['finals']), default=0):.1f}s")

    print("\n-- LLM API 请求 --")
    print(
        f"  base: n={len(base['calls'])} "
        f"总 {sum(d for d, _ in base['calls']):.1f}s",
    )
    print(
        f"  new:  n={len(new['calls'])} "
        f"总 {sum(d for d, _ in new['calls']):.1f}s",
    )

    # KV cache 命中观察：gpt-5 系自动 prefix cache ≥ 1024 tokens
    # 若输入长度相似但 call_s 明显下降，说明 cache 生效
    print("\n-- sem_wait 分布 --")
    for side, d in (("base", base["sem_waits"]), ("new", new["sem_waits"])):
        if not d:
            continue
        p50 = d[len(d) // 2]
        p90 = d[int(len(d) * 0.9)] if len(d) > 1 else d[0]
        print(f"  {side}: n={len(d)} sum={sum(d):.1f}s "
              f"p50={p50:.1f}s p90={p90:.1f}s max={d[-1]:.1f}s")


if __name__ == "__main__":
    main()
