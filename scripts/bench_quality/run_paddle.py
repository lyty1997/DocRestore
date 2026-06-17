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

"""benchmark：用 PaddleOCR-VL 对逐页 PNG 产出 markdown。

通过 DocRestore 自己的 EngineManager 驱动（vl 模式 = ppocr-server[vLLM,
ppocr_vlm 环境] + 客户端[ppocr_client 环境]），即产品里实际使用 PaddleOCR-VL
的方式——这是最忠实的对比基线。对 inputs/{formula_pdf,photos} 每张 PNG 调
engine.ocr，取 PageOCR.raw_text（VL markdown）写到 paddle/{set}/{stem}.md。

用法（在 docrestore 环境跑，由它再拉起两个子环境）：
    conda run -n docrestore python scripts/bench_quality/run_paddle.py --gpu-id 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

INPUTS = PROJECT_ROOT / "output" / "bench" / "quality" / "inputs"
OUT = PROJECT_ROOT / "output" / "bench" / "quality" / "paddle"
SETS = ("formula_pdf", "photos")


def _detect_conda_python(env_name: str) -> str:
    """查询 conda 环境的 python 路径；找不到返回空串。"""
    conda_bin = shutil.which("conda")
    if not conda_bin:
        return ""
    result = subprocess.run(  # noqa: S603 — conda_bin 来自 shutil.which
        [conda_bin, "run", "-n", env_name, "which", "python"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


async def _run(gpu_id: str) -> None:
    """初始化 PaddleOCR-VL 引擎后逐页 OCR。"""
    from docrestore.ocr.engine_manager import EngineManager
    from docrestore.pipeline.config import OCRConfig

    server_py = _detect_conda_python("ppocr_vlm")
    client_py = _detect_conda_python("ppocr_client")
    if not server_py or not client_py:
        msg = "未能定位 ppocr_vlm/ppocr_client conda 环境"
        raise RuntimeError(msg)

    config = OCRConfig(
        model="paddle-ocr/ppocr-v4",
        gpu_id=gpu_id,
        paddle_pipeline="vl",
        paddle_server_python=server_py,
        paddle_python=client_py,
    )
    manager = EngineManager(default_config=config, gpu_lock=asyncio.Lock())

    OUT.mkdir(parents=True, exist_ok=True)
    timings: dict[str, object] = {
        "engine": "paddle-ocr-vl",
        "pipeline_version": config.paddle_pipeline_version,
    }
    try:
        t_init = time.time()
        print("PaddleOCR-VL initialize（拉起 ppocr-server + client）...")
        await manager.ensure(
            config, on_progress=lambda m: print(f"  · {m}", flush=True),
        )
        engine = manager.engine
        if engine is None:
            msg = "EngineManager.ensure 返回空引擎"
            raise RuntimeError(msg)
        timings["init_elapsed_s"] = round(time.time() - t_init, 2)
        print(f"  初始化完成: {timings['init_elapsed_s']}s", flush=True)

        for set_name in SETS:
            out_dir = OUT / set_name
            out_dir.mkdir(parents=True, exist_ok=True)
            set_timings: list[dict[str, object]] = []
            for png in sorted((INPUTS / set_name).glob("*.png")):
                stem = png.stem
                page_dir = out_dir / stem
                page_dir.mkdir(parents=True, exist_ok=True)
                t0 = time.time()
                page = await engine.ocr(png, page_dir)
                elapsed = time.time() - t0
                md_text = page.raw_text or ""
                (out_dir / f"{stem}.md").write_text(md_text, encoding="utf-8")
                set_timings.append({
                    "stem": stem, "elapsed_s": round(elapsed, 2),
                    "md_chars": len(md_text),
                })
                print(
                    f"  [{set_name}] {stem}: {elapsed:.1f}s, "
                    f"{len(md_text)} chars", flush=True,
                )
            timings[set_name] = set_timings
    finally:
        await manager.shutdown()

    (OUT / "timing.json").write_text(
        json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\n✔ PaddleOCR-VL 完成，输出 {OUT}")


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="PaddleOCR-VL benchmark")
    parser.add_argument("--gpu-id", default="0")
    args = parser.parse_args()
    asyncio.run(_run(args.gpu_id))


if __name__ == "__main__":
    main()
