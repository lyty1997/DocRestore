#!/usr/bin/env python3
# Copyright 2026 @lyty1997

# mypy: ignore-errors
"""AGE-8 PaddleOCR-VL 关合并实测

测试 merge_layout_blocks=False 是否能拿到栏级独立 block，
解决 DSC06838 默认下被合并为单一 content block 的问题。

前置：scripts/start.sh ppocr-server 在跑（端口 8119）。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
# 让 vllm-server 的 127.0.0.1 不走代理
os.environ["no_proxy"] = "localhost,127.0.0.1," + os.environ.get("no_proxy", "")
os.environ["NO_PROXY"] = os.environ["no_proxy"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = PROJECT_ROOT / "output" / "age8-probe-vl-no-merge"
IMAGES_DIR = PROJECT_ROOT / "test_images" / "age8-spike"
SERVER_URL = "http://127.0.0.1:8119/v1"
MODEL = "PaddleOCR-VL-1.5-0.9B"
IMAGES = ["DSC06836", "DSC06838"]


def _extract_blocks(res):
    """从 PaddleOCR-VL 结果对象拿 parsing_res_list / blocks"""
    try:
        data = res.json
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    inner = data.get("res", data)
    # PP-StructureV3/PaddleOCR-VL 的输出结构：
    candidates = [
        inner.get("parsing_res_list"),
        inner.get("blocks"),
        inner.get("layout_parsing_result"),
    ]
    for c in candidates:
        if isinstance(c, list) and c:
            return c
    return []


def _summarize(blocks: list, image_size: tuple[int, int]) -> str:
    out: list[str] = []
    w, h = image_size
    out.append(f"image_size={w}x{h}")
    out.append(f"total_blocks={len(blocks)}")
    if blocks:
        labels = Counter(b.get("block_label") or b.get("label") or "?" for b in blocks)
        out.append(f"labels={dict(labels)}")
    out.append("")
    for i, b in enumerate(blocks):
        bbox = b.get("block_bbox") or b.get("bbox") or [0, 0, 0, 0]
        x1, y1, x2, y2 = (int(c) for c in bbox[:4])
        label = b.get("block_label") or b.get("label") or "?"
        text = (b.get("block_content") or b.get("content") or b.get("text") or "")
        text = text.replace("\n", "⏎")[:100]
        out.append(
            f"  {i:>3}. [{label:<16}] "
            f"x=[{x1:>5},{x2:>5}] ({x1/w:5.1%}-{x2/w:5.1%})  "
            f"y=[{y1:>5},{y2:>5}] ({y1/h:5.1%}-{y2/h:5.1%})  "
            f"text={text}"
        )
    return "\n".join(out) + "\n"


def main() -> int:
    from paddleocr import PaddleOCRVL
    from PIL import Image

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    # 两组实验：默认（合并） vs 关合并
    configs = [
        {"name": "merge_off", "merge_layout_blocks": False},
        {"name": "merge_on",  "merge_layout_blocks": True},
    ]

    for cfg in configs:
        print(f"\n=== loading PaddleOCRVL ({cfg['name']}) ===")
        try:
            ocr = PaddleOCRVL(
                vl_rec_backend="vllm-server",
                vl_rec_server_url=SERVER_URL,
                vl_rec_api_model_name=MODEL,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  init failed: {exc}")
            continue

        for stem in IMAGES:
            img_path = IMAGES_DIR / f"{stem}.JPG"
            if not img_path.exists():
                continue
            w, h = Image.open(img_path).size
            print(f"  {stem} ({w}x{h})")
            try:
                # 在 predict 时传 merge_layout_blocks
                output = ocr.predict(
                    str(img_path),
                    merge_layout_blocks=cfg["merge_layout_blocks"],
                )
            except TypeError:
                # 老版本不支持 predict 级参数；尝试 init 级
                try:
                    ocr = PaddleOCRVL(
                        vl_rec_backend="vllm-server",
                        vl_rec_server_url=SERVER_URL,
                        vl_rec_api_model_name=MODEL,
                        merge_layout_blocks=cfg["merge_layout_blocks"],
                    )
                    output = ocr.predict(str(img_path))
                except Exception as exc:  # noqa: BLE001
                    print(f"    init+predict 都失败: {exc}")
                    continue
            except Exception as exc:  # noqa: BLE001
                print(f"    predict failed: {exc}")
                continue

            out_dir = OUT_ROOT / cfg["name"] / stem
            out_dir.mkdir(parents=True, exist_ok=True)
            for res in output:
                try:
                    res.save_to_img(save_path=str(out_dir))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    res.save_to_json(save_path=str(out_dir))
                except Exception:  # noqa: BLE001
                    pass
                blocks = _extract_blocks(res)
                (out_dir / "blocks.jsonl").write_text(
                    "\n".join(
                        json.dumps(
                            {
                                "label": b.get("block_label") or b.get("label"),
                                "bbox": b.get("block_bbox") or b.get("bbox"),
                                "text": b.get("block_content") or b.get("content"),
                            },
                            ensure_ascii=False,
                        )
                        for b in blocks
                    ),
                    encoding="utf-8",
                )
                (out_dir / "summary.txt").write_text(
                    _summarize(blocks, (w, h)), encoding="utf-8",
                )
                labels = Counter(
                    (b.get("block_label") or b.get("label") or "?") for b in blocks
                )
                print(f"    {len(blocks)} blocks; labels={dict(labels)}")
    print(f"\n→ {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
