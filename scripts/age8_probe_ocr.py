#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

# mypy: ignore-errors

"""AGE-8 证据采集：直接调用 PaddleOCR worker 采集 raw parsing_res_list。

不走 PaddleOCREngine（避开 column_filter 裁剪重跑等干扰），只拿每张图
的原始 block（label/bbox/text）。

前置：scripts/start.sh ppocr-server 已在跑（端口 8119）。

用法：
    python scripts/age8_probe_ocr.py  \\
        [--images DSC06836,DSC06838] \\
        [--output output/age8-probe]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKER = PROJECT_ROOT / "scripts" / "paddle_ocr_worker.py"
PPOCR_PYTHON = "/home/lyty/work/ai/env/anaconda3/envs/ppocr_client/bin/python3.12"
SERVER_URL = "http://127.0.0.1:8119/v1"
MODEL = "PaddleOCR-VL-1.5-0.9B"


async def _send(proc, cmd: dict) -> None:
    proc.stdin.write((json.dumps(cmd) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    line = await proc.stdout.readline()
    if not line:
        raise RuntimeError("worker closed stdout unexpectedly")
    return json.loads(line)


async def _probe(images: list[str], output_root: Path) -> None:
    env = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
        "no_proxy": "localhost,127.0.0.1",
        "NO_PROXY": "localhost,127.0.0.1",
    }
    proc = await asyncio.create_subprocess_exec(
        PPOCR_PYTHON, str(WORKER),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,  # 直接透传 worker 的 stderr 日志
        env=env,
    )
    try:
        await _send(proc, {
            "cmd": "initialize",
            "server_url": SERVER_URL,
            "server_model_name": MODEL,
        })
        init = await _recv(proc)
        print(f"initialize: ok={init.get('ok')} error={init.get('error','')}")
        if not init.get("ok"):
            return

        for stem in images:
            out_dir = output_root / stem
            out_dir.mkdir(parents=True, exist_ok=True)
            img = PROJECT_ROOT / "test_images" / "age8-spike" / f"{stem}.JPG"
            if not img.exists():
                print(f"skip {stem}: {img} not found")
                continue
            print(f"\n=== {stem} ===")
            await _send(proc, {
                "cmd": "ocr",
                "image_path": str(img),
                "output_dir": str(out_dir),
                "min_image_size": 0,
            })
            resp = await _recv(proc)
            (out_dir / "response.json").write_text(
                json.dumps(resp, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not resp.get("ok"):
                print(f"  failed: {resp.get('error')}")
                continue
            coords = resp.get("coordinates", [])
            w, h = resp.get("image_size", [0, 0])
            print(f"  image_size={w}x{h}  blocks={len(coords)}")
            labels = Counter(c["label"] for c in coords)
            print(f"  labels: {dict(labels)}")
            print("  block bbox / text preview (first 20):")
            for c in coords[:20]:
                x1, y1, x2, y2 = c["bbox"]
                text = (c.get("text") or "").replace("\n", "⏎")
                print(
                    f"    [{c['label']:<16}] "
                    f"x=[{x1:>4},{x2:>4}] ({x1/w:5.1%}-{x2/w:5.1%})  "
                    f"y=[{y1:>4},{y2:>4}] ({y1/h:5.1%}-{y2/h:5.1%})  "
                    f"text={text[:80]}"
                )
            # 也 dump 到独立 jsonl 便于后续程序处理
            with (out_dir / "blocks.jsonl").open("w", encoding="utf-8") as f:
                for c in coords:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
    finally:
        try:
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, ProcessLookupError):
            proc.kill()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AGE-8 OCR 证据采集")
    parser.add_argument(
        "--images", default="DSC06836,DSC06838",
        help="逗号分隔的 stem 列表",
    )
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "output" / "age8-probe",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    images = [s.strip() for s in args.images.split(",") if s.strip()]
    asyncio.run(_probe(images, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
