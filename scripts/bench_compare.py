#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""汇总多次 bench_ocr.py 结果，输出对比表。

用法：
    python scripts/bench_compare.py \\
        output/bench/paddle_baseline \\
        output/bench/paddle_optimized \\
        output/bench/deepseek_baseline \\
        output/bench/deepseek_optimized

每个位置参数指向包含 summary.json + gpu_trace.csv 的 bench 输出目录。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BenchRecord:
    """单次 bench 的汇总记录。"""

    name: str
    engine: str
    preset: str
    num_images: int
    init_elapsed: float
    warmup_elapsed: float
    mean_run_elapsed: float
    throughput: float
    gpu_util_mean: float
    gpu_util_p95: float
    gpu_mem_peak_mib: float


def _parse_gpu_trace(trace: Path) -> tuple[float, float, float]:
    """读取 gpu_trace.csv，返回 (util_mean, util_p95, mem_peak_mib)。"""
    utils: list[float] = []
    mem: list[float] = []
    if not trace.exists():
        return 0.0, 0.0, 0.0
    with trace.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        # nvidia-smi 表头形如 ' utilization.gpu [%]'（带前导空格 + 单位后缀）
        util_col = _find_prefix(fieldnames, "utilization.gpu")
        mem_col = _find_prefix(fieldnames, "memory.used")
        if util_col is None or mem_col is None:
            msg = (
                f"gpu_trace.csv 缺少列（现有: {fieldnames}）"
            )
            raise KeyError(msg)
        for row in reader:
            try:
                util = float(row[util_col].strip())
                m = float(row[mem_col].strip())
            except (ValueError, AttributeError):
                continue
            utils.append(util)
            mem.append(m)

    if not utils:
        return 0.0, 0.0, 0.0
    mean_u = sum(utils) / len(utils)
    sorted_u = sorted(utils)
    p95_idx = max(0, int(len(sorted_u) * 0.95) - 1)
    p95_u = sorted_u[p95_idx]
    mem_peak = max(mem) if mem else 0.0
    return mean_u, p95_u, mem_peak


def _find_prefix(fieldnames: Sequence[str], prefix: str) -> str | None:
    """按 strip() 后 startswith(prefix) 匹配列名，返回原始列名。"""
    for name in fieldnames:
        if name.strip().startswith(prefix):
            return name
    return None


def _load(bench_dir: Path) -> BenchRecord:
    """从 bench 输出目录加载 summary.json + gpu_trace.csv。"""
    summary_path = bench_dir / "summary.json"
    if not summary_path.exists():
        msg = f"缺少 summary.json: {summary_path}"
        raise FileNotFoundError(msg)

    with summary_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    util_mean, util_p95, mem_peak = _parse_gpu_trace(
        bench_dir / "gpu_trace.csv",
    )

    return BenchRecord(
        name=bench_dir.name,
        engine=str(data.get("engine", "")),
        preset=str(data.get("preset", "")),
        num_images=int(data.get("num_images", 0)),
        init_elapsed=float(data.get("init_elapsed", 0.0)),
        warmup_elapsed=float(data.get("warmup_elapsed", 0.0)),
        mean_run_elapsed=float(data.get("mean_run_elapsed", 0.0)),
        throughput=float(data.get("mean_throughput_img_per_s", 0.0)),
        gpu_util_mean=util_mean,
        gpu_util_p95=util_p95,
        gpu_mem_peak_mib=mem_peak,
    )


def _render(records: list[BenchRecord]) -> str:
    """渲染对比表格（markdown）。"""
    header = (
        "| 名称 | 引擎 | 预设 | init(s) | warmup(s) | mean_run(s) | "
        "img/s | GPU_util_mean(%) | GPU_util_p95(%) | mem_peak(MiB) |"
    )
    sep = (
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"
    )
    rows: list[str] = [header, sep]
    for r in records:
        rows.append(
            f"| {r.name} | {r.engine} | {r.preset} | "
            f"{r.init_elapsed:.1f} | {r.warmup_elapsed:.1f} | "
            f"{r.mean_run_elapsed:.1f} | {r.throughput:.2f} | "
            f"{r.gpu_util_mean:.1f} | {r.gpu_util_p95:.1f} | "
            f"{r.gpu_mem_peak_mib:.0f} |"
        )
    return "\n".join(rows)


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="OCR bench 对比")
    parser.add_argument("dirs", nargs="+", help="bench 输出目录")
    parser.add_argument("--output", default="", help="汇总 markdown 输出路径")
    args = parser.parse_args()

    records: list[BenchRecord] = []
    for d in args.dirs:
        p = Path(d)
        if not p.is_dir():
            print(f"跳过非目录: {p}", file=sys.stderr)
            continue
        try:
            records.append(_load(p))
        except (FileNotFoundError, KeyError, ValueError) as exc:
            print(f"加载失败 {p}: {exc}", file=sys.stderr)

    if not records:
        print("没有可加载的 bench 结果", file=sys.stderr)
        sys.exit(1)

    table = _render(records)
    print(table)

    if args.output:
        Path(args.output).write_text(table + "\n", encoding="utf-8")
        print(f"\n✔ 汇总表已保存到 {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
