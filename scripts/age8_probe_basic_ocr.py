#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

# mypy: ignore-errors

"""AGE-8 基础 OCR 行级 bbox 证据采集

验证 PaddleOCR 非 VL pipeline（基于 DBNet + CRNN）对 IDE 截图是否
返回行级别的稳定 bbox，为"按 x1 聚类 → 栏识别"方案提供证据。

环境：ppocr_client conda 环境
    /home/lyty/work/ai/env/anaconda3/envs/ppocr_client/bin/python \\
        scripts/age8_probe_basic_ocr.py

输出：
  output/age8-probe-basic/<stem>/
    ├── lines.jsonl     # 每行：{bbox, text, score}
    ├── summary.txt     # label 分布 + 前 30 行 bbox 预览 + x1 聚类摘要
    ├── overlay.jpg     # 原图 + bbox 叠加
    └── res.json        # 原始 OCR 结果（paddleocr dump）
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = PROJECT_ROOT / "output" / "age8-probe-basic"
IMAGES_DIR = PROJECT_ROOT / "test_images" / "age8-spike"
IMAGE_STEMS = [
    "DSC06835", "DSC06836", "DSC06837", "DSC06838",
    "DSC06839", "DSC06840", "DSC06841", "DSC06842",
]


def _cluster_x1(x1_list: list[int], bandwidth: int = 50) -> list[tuple[int, int, int]]:
    """1D 线性聚类：按 x1 排序后，相邻 gap > bandwidth 开新簇。

    返回 list[(cluster_center, count, representative_x1_min)]
    """
    if not x1_list:
        return []
    sorted_x1 = sorted(x1_list)
    clusters: list[list[int]] = [[sorted_x1[0]]]
    for x in sorted_x1[1:]:
        if x - clusters[-1][-1] > bandwidth:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    return [
        (int(sum(c) / len(c)), len(c), min(c))
        for c in clusters
    ]


def _summarize(
    lines_data: list[dict],
    image_size: tuple[int, int],
) -> str:
    """汇总行级 bbox 观察 + x1 聚类统计"""
    out: list[str] = []
    w, h = image_size
    out.append(f"image_size={w}x{h}")
    out.append(f"total_lines={len(lines_data)}")
    if not lines_data:
        return "\n".join(out) + "\n"

    # x1 聚类（关键验证：能否分出 N 栏）
    x1s = [ln["bbox"][0] for ln in lines_data]
    out.append("")
    out.append("x1 clusters (bandwidth=50px):")
    clusters = _cluster_x1(x1s, bandwidth=50)
    for i, (center, count, xmin) in enumerate(clusters):
        pct = center / w if w else 0
        out.append(
            f"  cluster {i}: center={center} ({pct:.1%})  count={count}  xmin={xmin}"
        )

    # 各大簇的代表样本（按簇分组 + 每簇前 3 行）
    out.append("")
    out.append("sample lines per cluster:")
    for i, (center, _, _) in enumerate(clusters):
        members = [
            ln for ln in lines_data
            if abs(ln["bbox"][0] - center) <= 50
        ]
        out.append(
            f"  cluster {i} (x1≈{center}, {len(members)} lines):"
        )
        for ln in members[:3]:
            x1, y1, x2, y2 = ln["bbox"]
            text = ln.get("text", "")[:100].replace("\n", "⏎")
            out.append(
                f"    y=[{y1:>4},{y2:>4}]  x2={x2:>5}  score={ln.get('score',0):.2f}  {text}"
            )

    # y 分布（头部/底部可能的 tab/terminal 行）
    out.append("")
    sorted_y = sorted(lines_data, key=lambda ln: ln["bbox"][1])
    out.append("first 5 lines by y (top of image):")
    for ln in sorted_y[:5]:
        x1, y1, x2, y2 = ln["bbox"]
        text = ln.get("text", "")[:80].replace("\n", "⏎")
        out.append(f"  x=[{x1:>5},{x2:>5}] y=[{y1:>4},{y2:>4}]  {text}")
    out.append("")
    out.append("last 5 lines by y (bottom):")
    for ln in sorted_y[-5:]:
        x1, y1, x2, y2 = ln["bbox"]
        text = ln.get("text", "")[:80].replace("\n", "⏎")
        out.append(f"  x=[{x1:>5},{x2:>5}] y=[{y1:>4},{y2:>4}]  {text}")
    return "\n".join(out) + "\n"


def _probe() -> None:
    from paddleocr import PaddleOCR  # type: ignore[import-not-found]
    from PIL import Image  # type: ignore[import-not-found]

    print("=== loading PaddleOCR ===")
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )

    for stem in IMAGE_STEMS:
        img_path = IMAGES_DIR / f"{stem}.JPG"
        if not img_path.exists():
            print(f"skip {stem}: {img_path} not found")
            continue
        out_dir = OUT_ROOT / stem
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {stem} ===")

        image_size = Image.open(img_path).size
        output = ocr.predict(str(img_path))

        for res in output:
            # save 标准 artefacts
            try:
                res.save_to_img(save_path=str(out_dir))
            except Exception as exc:  # noqa: BLE001
                print(f"  save_to_img failed: {exc}")
            try:
                res.save_to_json(save_path=str(out_dir))
            except Exception as exc:  # noqa: BLE001
                print(f"  save_to_json failed: {exc}")

            # 提取行级数据
            data = res.json
            inner = data.get("res", data) if isinstance(data, dict) else {}
            rec_boxes = inner.get("rec_boxes") or []
            rec_texts = inner.get("rec_texts") or []
            rec_scores = inner.get("rec_scores") or []
            lines_data = []
            for i, box in enumerate(rec_boxes):
                if len(box) < 4:
                    continue
                x1, y1, x2, y2 = (int(v) for v in box[:4])
                lines_data.append({
                    "bbox": [x1, y1, x2, y2],
                    "text": rec_texts[i] if i < len(rec_texts) else "",
                    "score": float(rec_scores[i]) if i < len(rec_scores) else 0.0,
                })
            with (out_dir / "lines.jsonl").open("w", encoding="utf-8") as f:
                for ln in lines_data:
                    f.write(json.dumps(ln, ensure_ascii=False) + "\n")
            (out_dir / "summary.txt").write_text(
                _summarize(lines_data, image_size), encoding="utf-8",
            )
            print(f"  {len(lines_data)} lines detected")


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    _probe()
    print(f"\nall done → {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
