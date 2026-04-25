#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

# mypy: ignore-errors

"""AGE-8 LayoutDetection 独立模块证据采集

测试 3 个候选 layout 模型对 IDE 截图的区域识别效果，选出最适合的：
  - PP-DocBlockLayout  : 1 类，多栏文档子区域（最可能贴合多栏编辑器）
  - PP-DocLayoutV2     : 25 类 + reading order
  - PP-DocLayout_plus-L: 20 类

环境：必须在 ppocr_client conda 环境运行。
    /home/lyty/work/ai/env/anaconda3/envs/ppocr_client/bin/python \\
        scripts/age8_probe_layout.py

输出：
  output/age8-probe-layout/<model_name>/<stem>/
    ├── layout_overlay.jpg   # 可视化（原图 + bbox 叠加）
    ├── res.json             # 原始检测结果
    └── summary.txt          # 人类可读摘要（label 分布 + bbox 列表）
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = PROJECT_ROOT / "output" / "age8-probe-layout"
IMAGES_DIR = PROJECT_ROOT / "test_images" / "age8-spike"

MODELS = [
    "PP-DocLayout_plus-L",
    "PP-DocLayout-L",
]
IMAGE_STEMS = ["DSC06836", "DSC06838"]


def _extract_boxes(res) -> list[dict]:
    """从 LayoutDetection 结果对象拿 boxes。多个版本的 res 接口略有差异。"""
    # res.json 是 dict：{"res": {"boxes": [...]}}
    try:
        data = res.json
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, dict):
        inner = data.get("res", data)
        boxes = inner.get("boxes") if isinstance(inner, dict) else None
        if isinstance(boxes, list):
            return boxes
    # 兜底：尝试 attribute 访问
    if hasattr(res, "boxes"):
        return list(res.boxes)
    return []


def _summarize(boxes: list[dict], image_size: tuple[int, int] | None) -> str:
    """人眼可读摘要"""
    if not boxes:
        return "no boxes detected\n"
    lines: list[str] = []
    labels = Counter(b.get("label", "?") for b in boxes)
    lines.append(f"total={len(boxes)}  labels={dict(labels)}")
    if image_size:
        w, h = image_size
        lines.append(f"image_size={w}x{h}")
    lines.append("")
    lines.append("boxes:")
    for i, b in enumerate(boxes):
        coord = b.get("coordinate") or b.get("bbox") or [0, 0, 0, 0]
        x1, y1, x2, y2 = (int(x) for x in coord[:4])
        label = b.get("label", "?")
        score = b.get("score", 0.0)
        extra = ""
        if image_size:
            w, h = image_size
            extra = (
                f"  ({x1/w:5.1%}-{x2/w:5.1%} × {y1/h:5.1%}-{y2/h:5.1%})"
                if w > 0 and h > 0 else ""
            )
        lines.append(
            f"  {i:>3}. [{label:<24}] "
            f"x=[{x1:>5},{x2:>5}] y=[{y1:>5},{y2:>5}]{extra}  "
            f"score={score:.2f}"
        )
    return "\n".join(lines) + "\n"


def _probe_model(model_name: str) -> None:
    """实例化 + 对每张图 predict + 导出可视化/json/summary"""
    from paddleocr import LayoutDetection  # type: ignore[import-not-found]
    from PIL import Image  # type: ignore[import-not-found]

    print(f"\n=== loading {model_name} ===")
    try:
        model = LayoutDetection(model_name=model_name)
    except Exception as exc:  # noqa: BLE001
        print(f"  failed to load: {exc}")
        return

    for stem in IMAGE_STEMS:
        img_path = IMAGES_DIR / f"{stem}.JPG"
        if not img_path.exists():
            print(f"  skip {stem}: {img_path} not found")
            continue
        out_dir = OUT_ROOT / model_name / stem
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"  {stem} ...")

        image_size = Image.open(img_path).size
        try:
            output = model.predict(
                str(img_path), batch_size=1, layout_nms=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    predict failed: {exc}")
            continue

        for res in output:
            try:
                res.save_to_img(save_path=str(out_dir))
            except Exception as exc:  # noqa: BLE001
                print(f"    save_to_img failed: {exc}")
            try:
                res.save_to_json(save_path=str(out_dir))
            except Exception as exc:  # noqa: BLE001
                print(f"    save_to_json failed: {exc}")

            boxes = _extract_boxes(res)
            (out_dir / "summary.txt").write_text(
                _summarize(boxes, image_size), encoding="utf-8",
            )
            # dump bbox 供下游脚本分析
            with (out_dir / "boxes.jsonl").open("w", encoding="utf-8") as f:
                for b in boxes:
                    f.write(json.dumps(b, ensure_ascii=False) + "\n")
            print(
                f"    ok: {len(boxes)} boxes; "
                f"labels={dict(Counter(b.get('label','?') for b in boxes))}"
            )


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for model_name in MODELS:
        _probe_model(model_name)
    print(f"\nall done → {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
