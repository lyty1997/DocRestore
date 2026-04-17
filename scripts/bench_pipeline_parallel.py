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

"""Pipeline 级并行吞吐基准

对比同样 N 份输入的两种运行模式：
  - serial   逐个 task 顺序跑（max_concurrent_llm_requests 对吞吐无影响）
  - parallel 所有 task 通过 asyncio.gather 同时跑，共享全局 gpu_lock 和
             llm_semaphore

用法示例：
    conda activate docrestore && source .env
    python scripts/bench_pipeline_parallel.py \
        --input test_images/ocr_sample --repeat 3 --llm-concurrency 3

各 task 输出被写到 output/bench_pipeline_parallel/{mode}/task_{i}/ 下，
并从每个 task 的 profile.json 读取 stage 分布汇总。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

if TYPE_CHECKING:
    from docrestore.pipeline.pipeline import Pipeline


def _detect_conda_python(env_name: str) -> str:
    conda_bin = shutil.which("conda")
    if not conda_bin:
        return ""
    try:
        result = subprocess.run(  # noqa: S603 — conda_bin 来自 shutil.which
            [conda_bin, "run", "-n", env_name, "which", "python"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


async def _run_single_task(
    pipeline: Pipeline,
    image_dir: Path,
    output_dir: Path,
    tag: str,
) -> tuple[str, float]:
    """跑一个 task，返回 (tag, elapsed_seconds)。"""
    t0 = time.time()
    await pipeline.process_tree(
        image_dir=image_dir,
        output_dir=output_dir,
    )
    return tag, time.time() - t0


def _read_profile_summary(output_dir: Path) -> dict[str, Any]:
    """从 output_dir/profile.json 聚合 stage 耗时。"""
    profile_path = output_dir / "profile.json"
    if not profile_path.exists():
        return {}
    try:
        events = json.loads(profile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    summary: dict[str, dict[str, float]] = {}
    for e in events:
        name = e.get("name", "")
        dur = float(e.get("duration_s", 0.0))
        agg = summary.setdefault(name, {"count": 0.0, "total_s": 0.0})
        agg["count"] += 1
        agg["total_s"] += dur
    # 保留前 8 个最耗时 stage
    top = sorted(
        summary.items(), key=lambda kv: kv[1]["total_s"], reverse=True,
    )[:8]
    return dict(top)


async def _bench_mode(
    mode: str,
    image_dir: Path,
    n_tasks: int,
    out_root: Path,
    llm_concurrency: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """跑 mode 下的 N 个任务，返回基准记录。"""
    from docrestore.pipeline.config import (
        LLMConfig,
        OCRConfig,
        PipelineConfig,
    )
    from docrestore.pipeline.pipeline import Pipeline
    from docrestore.pipeline.scheduler import PipelineScheduler

    api_key = os.environ.get(args.llm_api_key_env, "")
    if not api_key:
        msg = f"环境变量 {args.llm_api_key_env} 未设置"
        raise RuntimeError(msg)

    config = PipelineConfig(
        ocr=OCRConfig(
            model=args.ocr_model,
            model_path=args.ocr_model_path,
            gpu_memory_utilization=args.ocr_gpu_util,
            max_model_len=args.ocr_max_model_len,
            max_tokens=args.ocr_max_tokens,
            paddle_python=args.paddle_python,
            paddle_server_url=args.paddle_server_url,
            paddle_server_model_name=args.paddle_server_model,
            deepseek_python=args.deepseek_python,
        ),
        llm=LLMConfig(
            model=args.llm_model,
            api_base=args.llm_api_base,
            api_key=api_key,
            max_retries=3,
            timeout=600,
            max_concurrent_requests=llm_concurrency,
        ),
    )

    # 全局共享的 scheduler（gpu_lock + llm_semaphore）
    scheduler = PipelineScheduler(
        max_concurrent_llm_requests=llm_concurrency,
    )

    # 每个 task 一个 Pipeline 实例（共享 OCR 引擎通常更高效，但当前代码
    # 路径里 Pipeline 本身持 engine；对比重点是 LLM 限流 + 并发编排，
    # 所以各自 Pipeline 只做一次 initialize 后复用）
    pipeline = Pipeline(config)
    pipeline.set_llm_semaphore(scheduler.llm_semaphore)
    await pipeline.initialize()

    # 输出目录：out_root/{mode}/task_{i}
    out_dirs = [out_root / mode / f"task_{i}" for i in range(n_tasks)]
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    if mode == "serial":
        per_task: list[tuple[str, float]] = []
        for i, d in enumerate(out_dirs):
            per_task.append(
                await _run_single_task(pipeline, image_dir, d, f"t{i}"),
            )
    elif mode == "parallel":
        per_task = list(await asyncio.gather(*(
            _run_single_task(pipeline, image_dir, d, f"t{i}")
            for i, d in enumerate(out_dirs)
        )))
    else:
        msg = f"未知 mode: {mode}"
        raise ValueError(msg)
    wall = time.time() - t0

    await pipeline.shutdown()

    profiles = {
        tag: _read_profile_summary(d)
        for (tag, _), d in zip(per_task, out_dirs, strict=True)
    }

    return {
        "mode": mode,
        "n_tasks": n_tasks,
        "llm_concurrency": llm_concurrency,
        "wall_seconds": wall,
        "per_task_seconds": dict(per_task),
        "profiles": profiles,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline 级并行吞吐基准",
    )
    parser.add_argument(
        "-i", "--input",
        default="test_images/ocr_sample",
        help="输入图片根目录（相对项目根）",
    )
    parser.add_argument(
        "-n", "--repeat", type=int, default=3,
        help="每种模式下重复的 task 数（默认 3）",
    )
    parser.add_argument(
        "--llm-concurrency", type=int, default=3,
        help="LLMConfig.max_concurrent_requests（默认 3）",
    )
    parser.add_argument(
        "--modes", default="serial,parallel",
        help="逗号分隔的模式列表，默认 serial,parallel",
    )
    parser.add_argument(
        "-o", "--output",
        default="output/bench_pipeline_parallel",
        help="输出根目录（相对项目根）",
    )
    # OCR / LLM 参数（与 run_e2e.py 对齐）
    parser.add_argument(
        "--ocr-model", default="paddle-ocr/ppocr-v4",
    )
    parser.add_argument("--paddle-python", default="")
    parser.add_argument(
        "--paddle-server-url", default="http://localhost:8119/v1",
    )
    parser.add_argument(
        "--paddle-server-model", default="PaddleOCR-VL-1.5-0.9B",
    )
    parser.add_argument("--deepseek-python", default="")
    parser.add_argument(
        "--ocr-model-path", default="models/DeepSeek-OCR-2",
    )
    parser.add_argument("--ocr-gpu-util", type=float, default=0.9)
    parser.add_argument("--ocr-max-model-len", type=int, default=8192)
    parser.add_argument("--ocr-max-tokens", type=int, default=8192)
    parser.add_argument(
        "--llm-model",
        default="openai/gemini-3.1-flash-lite-preview",
    )
    parser.add_argument(
        "--llm-api-base", default="https://poloai.top/v1",
    )
    parser.add_argument(
        "--llm-api-key-env", default="GEMINI_API_KEY",
    )

    args = parser.parse_args()

    image_dir = PROJECT_ROOT / args.input
    if not image_dir.exists():
        print(f"错误：输入目录不存在 {image_dir}")
        sys.exit(1)

    # 自动补全 OCR client python
    if not args.paddle_python and args.ocr_model.startswith("paddle"):
        args.paddle_python = _detect_conda_python("ppocr_client")
    if (
        not args.deepseek_python
        and args.ocr_model.startswith("deepseek")
    ):
        args.deepseek_python = _detect_conda_python("deepseek_ocr")

    out_root = PROJECT_ROOT / args.output
    out_root.mkdir(parents=True, exist_ok=True)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    print(f"输入目录: {image_dir}")
    print(f"任务数: {args.repeat}, LLM 并发上限: {args.llm_concurrency}")
    print(f"模式: {modes}\n")

    records: list[dict[str, Any]] = []
    for mode in modes:
        print(f"=== 模式 {mode} ===")
        rec = await _bench_mode(
            mode=mode,
            image_dir=image_dir,
            n_tasks=args.repeat,
            out_root=out_root,
            llm_concurrency=args.llm_concurrency,
            args=args,
        )
        records.append(rec)
        print(
            f"  wall={rec['wall_seconds']:.1f}s, "
            f"per_task={list(rec['per_task_seconds'].values())}",
        )

    # 汇总输出
    summary_path = out_root / "summary.json"
    summary_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n汇总写入: {summary_path}")

    # 对比一行
    if len(records) >= 2:
        s = next(
            (r for r in records if r["mode"] == "serial"), None,
        )
        p = next(
            (r for r in records if r["mode"] == "parallel"), None,
        )
        if s and p:
            speedup = s["wall_seconds"] / p["wall_seconds"]
            print(
                f"\n并发 vs 串行 wall-time speedup: "
                f"{speedup:.2f}×（目标 ≥ 1.67× 即 ≤ 0.6× 耗时）",
            )


if __name__ == "__main__":
    asyncio.run(main())
