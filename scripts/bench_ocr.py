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

"""OCR benchmark — 基线 vs 优化 对比

为 PaddleOCR 与 DeepSeek-OCR-2 两引擎分别跑 baseline/optimized 预设，
采集端到端耗时、每页延迟、GPU 利用率时序，输出到独立目录。

用法：
    conda activate docrestore && source .env

    # PaddleOCR 基线
    python scripts/bench_ocr.py \\
        --engine paddle-ocr --preset baseline \\
        --output output/bench/paddle_baseline --gpu-id 0

    # PaddleOCR 优化（写 paddle_backend_config.yaml 传给 ppocr-server）
    python scripts/bench_ocr.py \\
        --engine paddle-ocr --preset optimized \\
        --output output/bench/paddle_optimized --gpu-id 0

    # DeepSeek-OCR-2 基线 / 优化
    python scripts/bench_ocr.py \\
        --engine deepseek-ocr-2 --preset baseline \\
        --output output/bench/deepseek_baseline --gpu-id 0
    python scripts/bench_ocr.py \\
        --engine deepseek-ocr-2 --preset optimized \\
        --output output/bench/deepseek_optimized --gpu-id 0

数据产物：
    output_dir/
      summary.json       - 汇总指标（init/warmup/per-run 时长、均值吞吐）
      per_page.csv       - 每页延迟（run_idx, image_path, elapsed_s）
      gpu_trace.csv      - nvidia-smi 时序（采样期覆盖 initialize→shutdown）
      paddle_backend_config.yaml  - PaddleOCR 优化参数（仅 paddle-ocr optimized）
      pages_ocr/run{N}/  - OCR 输出目录（每个 run 独立，避免增量跳过）
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

if TYPE_CHECKING:
    from docrestore.pipeline.config import OCRConfig


# ── PaddleOCR 优化配置（写成 YAML 由 paddlex 转成 vLLM CLI 参数） ──
# paddlex 只透传 YAML 中出现的键，不出现的仍用 PaddleOCR 默认（含
# gpu-memory-utilization=0.85 / max-model-len=16384 / api-server-count=4 等）。
# 这里只覆盖两引擎共有的 5 项优化。
# 注意：vLLM CLI --block-size 仅接受 {1,8,16,32,64,128}，官方参考脚本的 256
#       仅在 Python API 内部有效，CLI 路径要降到 128。
PADDLE_OPTIMIZED_BACKEND_CONFIG: dict[str, object] = {
    "block_size": 128,
    "swap_space": 0,
    "enforce_eager": True,
    "disable_mm_preprocessor_cache": True,
    "disable_log_stats": True,
}


@dataclass
class PagePerf:
    """单页 OCR 延迟记录。"""

    image_path: str
    run_idx: int
    elapsed: float


def _collect_images(images_root: Path, subdirs: list[str]) -> list[Path]:
    """从 images_root 下指定子目录按文件名序收集 JPG/PNG 图片。"""
    exts = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
    images: list[Path] = []
    for sub in subdirs:
        d = images_root / sub
        if not d.is_dir():
            print(f"子目录不存在: {d}", file=sys.stderr)
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix in exts:
                images.append(p)
    return images


def _detect_conda_python(env_name: str) -> str:
    """查询 conda 环境的 python 路径；找不到返回空串。"""
    conda_bin = shutil.which("conda")
    if not conda_bin:
        return ""
    try:
        result = subprocess.run(  # noqa: S603 — conda_bin 来自 shutil.which
            [conda_bin, "run", "-n", env_name, "which", "python"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _build_paddle_config(
    preset: str, gpu_id: str, output_dir: Path,
) -> OCRConfig:
    """为 PaddleOCR 构造 OCRConfig。optimized 预设写 YAML 经 paddlex 透传。"""
    from docrestore.pipeline.config import OCRConfig

    paddle_server_py = _detect_conda_python("ppocr_vlm")
    paddle_client_py = _detect_conda_python("ppocr_client")
    if not paddle_server_py or not paddle_client_py:
        msg = (
            "未能定位 ppocr_vlm/ppocr_client conda 环境，"
            "请运行 scripts/setup_paddle_ocr.sh"
        )
        raise RuntimeError(msg)

    backend_config_path = ""
    if preset == "optimized":
        output_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = output_dir / "paddle_backend_config.yaml"
        with yaml_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                PADDLE_OPTIMIZED_BACKEND_CONFIG, f, sort_keys=False,
            )
        backend_config_path = str(yaml_path)

    return OCRConfig(
        model="paddle-ocr/ppocr-v4",
        gpu_id=gpu_id,
        paddle_server_python=paddle_server_py,
        paddle_python=paddle_client_py,
        paddle_server_backend_config=backend_config_path,
    )


def _build_deepseek_config(preset: str, gpu_id: str) -> OCRConfig:
    """为 DeepSeek-OCR-2 构造 OCRConfig。optimized 对齐官方 run_dpsk_ocr2_pdf.py。"""
    from docrestore.pipeline.config import OCRConfig

    deepseek_py = _detect_conda_python("deepseek_ocr")
    if not deepseek_py:
        msg = (
            "未能定位 deepseek_ocr conda 环境，"
            "请运行 scripts/setup_deepseek_ocr.sh"
        )
        raise RuntimeError(msg)

    if preset == "optimized":
        # block_size=128：与 PaddleOCR 预设保持一致（CLI 允许上限）。
        # 官方 run_dpsk_ocr2_pdf.py 用 256，但那是 Python API 直构，
        # 我们通过 AsyncEngineArgs(**kwargs) 走同一校验链路，取兼容值。
        return OCRConfig(
            model="deepseek/ocr-2",
            gpu_id=gpu_id,
            deepseek_python=deepseek_py,
            gpu_memory_utilization=0.9,
            vllm_enforce_eager=False,
            vllm_block_size=128,
            vllm_swap_space_gb=0.0,
            vllm_disable_mm_preprocessor_cache=True,
            vllm_disable_log_stats=True,
        )
    return OCRConfig(
        model="deepseek/ocr-2",
        gpu_id=gpu_id,
        deepseek_python=deepseek_py,
    )


def _build_config(
    engine: str, preset: str, gpu_id: str, output_dir: Path,
) -> OCRConfig:
    """根据 engine/preset 分派到对应的构造器。"""
    if engine == "paddle-ocr":
        return _build_paddle_config(preset, gpu_id, output_dir)
    if engine == "deepseek-ocr-2":
        return _build_deepseek_config(preset, gpu_id)
    msg = f"未知引擎: {engine}"
    raise ValueError(msg)


def _start_gpu_sampler(
    gpu_id: str, out_csv: Path, interval_ms: int,
) -> subprocess.Popen[bytes]:
    """后台启动 gpu_sampler.py，写 CSV 时序。"""
    sampler_script = PROJECT_ROOT / "scripts" / "gpu_sampler.py"
    return subprocess.Popen(  # noqa: S603 — 参数全由本脚本组装
        [
            sys.executable, str(sampler_script),
            "--gpu-id", gpu_id,
            "--interval-ms", str(interval_ms),
            "--output", str(out_csv),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # 独立进程组，避免被主进程的 signal handler 连带杀掉
        start_new_session=True,
    )


def _stop_gpu_sampler(proc: subprocess.Popen[bytes]) -> None:
    """优雅停止 gpu_sampler：SIGTERM → wait 5s → SIGKILL 兜底。"""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


async def _bench(
    *,
    engine_name: str,
    preset: str,
    images: list[Path],
    gpu_id: str,
    output_dir: Path,
    warmup: int,
    runs: int,
    gpu_sample_interval_ms: int,
) -> dict[str, object]:
    """跑 benchmark 主流程：起引擎 → warmup → N 轮遍历 → 关引擎。"""
    from docrestore.ocr.engine_manager import EngineManager

    pages_root = output_dir / "pages_ocr"
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(pages_root.mkdir, exist_ok=True)

    config = _build_config(engine_name, preset, gpu_id, output_dir)
    gpu_lock = asyncio.Lock()
    manager = EngineManager(default_config=config, gpu_lock=gpu_lock)

    # 启动 GPU 采样器
    gpu_trace = output_dir / "gpu_trace.csv"
    sampler = _start_gpu_sampler(gpu_id, gpu_trace, gpu_sample_interval_ms)

    metrics: dict[str, object] = {
        "engine": engine_name,
        "preset": preset,
        "model": config.model,
        "gpu_id": gpu_id,
        "num_images": len(images),
        "warmup": warmup,
        "runs": runs,
        "gpu_sample_interval_ms": gpu_sample_interval_ms,
    }
    per_page: list[PagePerf] = []

    try:
        # 1) 初始化引擎
        t0 = time.time()
        print(f"[{engine_name}/{preset}] initialize...", flush=True)
        await manager.ensure(
            config, on_progress=lambda m: print(f"  · {m}", flush=True),
        )
        engine = manager.engine
        if engine is None:
            msg = "EngineManager.ensure 返回空引擎"
            raise RuntimeError(msg)
        init_elapsed = time.time() - t0
        metrics["init_elapsed"] = init_elapsed
        print(f"  initialize done: {init_elapsed:.1f}s", flush=True)

        # 2) Warmup（不计入延迟统计）
        warmup_dir = pages_root / "warmup"
        if warmup > 0 and images:
            await asyncio.to_thread(warmup_dir.mkdir, exist_ok=True)
            print(
                f"[{engine_name}/{preset}] warmup {warmup} images...",
                flush=True,
            )
            t_warmup = time.time()
            for img in images[:warmup]:
                await engine.ocr(img, warmup_dir)
            warmup_elapsed = time.time() - t_warmup
            metrics["warmup_elapsed"] = warmup_elapsed
            print(f"  warmup done: {warmup_elapsed:.1f}s", flush=True)

        # 3) 真实 runs：每 run 用独立目录避免增量跳过
        run_elapsed: list[float] = []
        for run_idx in range(1, runs + 1):
            run_dir = pages_root / f"run{run_idx}"
            if await asyncio.to_thread(run_dir.exists):
                await asyncio.to_thread(shutil.rmtree, run_dir)
            await asyncio.to_thread(run_dir.mkdir)
            print(
                f"[{engine_name}/{preset}] run {run_idx}/{runs}...",
                flush=True,
            )
            t_run = time.time()
            for img in images:
                t_page = time.time()
                await engine.ocr(img, run_dir)
                per_page.append(PagePerf(
                    image_path=str(img.relative_to(PROJECT_ROOT)),
                    run_idx=run_idx,
                    elapsed=time.time() - t_page,
                ))
            elapsed = time.time() - t_run
            run_elapsed.append(elapsed)
            print(
                f"  run {run_idx} done: {elapsed:.1f}s "
                f"({elapsed / len(images):.2f}s/img)",
                flush=True,
            )

        metrics["run_elapsed"] = run_elapsed
        metrics["mean_run_elapsed"] = sum(run_elapsed) / len(run_elapsed)
        metrics["mean_throughput_img_per_s"] = (
            len(images) / (sum(run_elapsed) / len(run_elapsed))
        )
    finally:
        try:
            await manager.shutdown()
        finally:
            _stop_gpu_sampler(sampler)

    # 4) 落盘
    csv_path = output_dir / "per_page.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run_idx", "image_path", "elapsed_s"])
        for p in per_page:
            w.writerow([p.run_idx, p.image_path, f"{p.elapsed:.4f}"])

    summary_path = output_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    return metrics


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="OCR benchmark")
    parser.add_argument(
        "--engine", choices=["paddle-ocr", "deepseek-ocr-2"], required=True,
    )
    parser.add_argument(
        "--preset", choices=["baseline", "optimized"], required=True,
    )
    parser.add_argument(
        "--images-root", default="test_images",
        help="图片根目录（相对项目根）",
    )
    parser.add_argument(
        "--subdirs",
        default="Linux_SDK_导读,Linux_SDK_开发指南",
        help="逗号分隔的子目录名",
    )
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--gpu-id", default="0")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument(
        "--gpu-sample-interval-ms", type=int, default=500,
        help="nvidia-smi 采样间隔（毫秒）",
    )
    args = parser.parse_args()

    images_root = PROJECT_ROOT / args.images_root
    output_dir = PROJECT_ROOT / args.output
    subdirs = [s.strip() for s in args.subdirs.split(",") if s.strip()]

    images = _collect_images(images_root, subdirs)
    if not images:
        print(
            f"错误：未找到任何图片（{images_root} / {subdirs}）",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"{args.engine}/{args.preset}: {len(images)} 张图片，"
        f"warmup={args.warmup}, runs={args.runs}，输出 {output_dir}",
    )

    metrics = asyncio.run(_bench(
        engine_name=args.engine,
        preset=args.preset,
        images=images,
        gpu_id=args.gpu_id,
        output_dir=output_dir,
        warmup=args.warmup,
        runs=args.runs,
        gpu_sample_interval_ms=args.gpu_sample_interval_ms,
    ))

    print(
        "\n✔ summary: init={init:.1f}s, mean_run={mr:.1f}s, "
        "throughput={tp:.2f} img/s".format(
            init=float(metrics["init_elapsed"]),  # type: ignore[arg-type]
            mr=float(metrics["mean_run_elapsed"]),  # type: ignore[arg-type]
            tp=float(metrics["mean_throughput_img_per_s"]),  # type: ignore[arg-type]
        ),
    )
    print(f"  详情: {output_dir}/summary.json")


if __name__ == "__main__":
    main()
