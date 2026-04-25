#!/usr/bin/env python3
# Copyright 2026 @lyty1997

# mypy: ignore-errors
"""AGE-8 PP-DocBlockLayout 低阈值再验证

之前实测 PP-DocBlockLayout 默认阈值下对 IDE 图返回单一 Region。
按 explore agent 提示降低 thresholdeshold 重试，看是否能切出多栏子区域。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = PROJECT_ROOT / "output" / "age8-probe-block-threshold"
IMAGES_DIR = PROJECT_ROOT / "test_images" / "age8-spike"

# 多组 thresholdeshold 实验
THRESHOLDS = [0.5, 0.3, 0.1, 0.05]
IMAGES = ["DSC06836", "DSC06838"]


def _extract_boxes(res):
    try:
        data = res.json
        if isinstance(data, dict):
            inner = data.get("res", data)
            if isinstance(inner, dict):
                boxes = inner.get("boxes")
                if isinstance(boxes, list):
                    return boxes
    except Exception:  # noqa: BLE001
        pass
    return []


def main() -> int:
    from paddleocr import LayoutDetection
    from PIL import Image

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    print("=== loading PP-DocBlockLayout ===")
    model = LayoutDetection(model_name="PP-DocBlockLayout")

    for stem in IMAGES:
        img_path = IMAGES_DIR / f"{stem}.JPG"
        if not img_path.exists():
            print(f"skip {stem}: {img_path}")
            continue
        w, h = Image.open(img_path).size
        print(f"\n=== {stem} ({w}x{h}) ===")
        for threshold in THRESHOLDS:
            try:
                output = model.predict(
                    str(img_path), batch_size=1, layout_nms=True,
                    threshold=threshold,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  threshold={threshold}: predict failed: {exc}")
                continue
            for res in output:
                boxes = _extract_boxes(res)
                labels = Counter(b.get("label", "?") for b in boxes)
                print(f"  threshold={threshold}: {len(boxes)} boxes; labels={dict(labels)}")
                out_dir = OUT_ROOT / stem / f"threshold_{threshold}"
                out_dir.mkdir(parents=True, exist_ok=True)
                try:
                    res.save_to_img(save_path=str(out_dir))
                except Exception as exc:  # noqa: BLE001
                    print(f"    save_to_img failed: {exc}")
                with (out_dir / "boxes.jsonl").open("w", encoding="utf-8") as f:
                    for b in boxes:
                        f.write(json.dumps(b, ensure_ascii=False) + "\n")
                # 简短摘要
                lines = [f"threshold={threshold}, total={len(boxes)}"]
                for i, b in enumerate(boxes):
                    coord = b.get("coordinate") or b.get("bbox") or [0,0,0,0]
                    x1, y1, x2, y2 = (int(c) for c in coord[:4])
                    lines.append(
                        f"  {i:>3}. [{b.get('label','?'):<10}] "
                        f"x=[{x1:>5},{x2:>5}] ({x1/w:5.1%}-{x2/w:5.1%})  "
                        f"y=[{y1:>5},{y2:>5}] ({y1/h:5.1%}-{y2/h:5.1%})  "
                        f"score={b.get('score',0):.3f}"
                    )
                (out_dir / "summary.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8",
                )
    print(f"\n→ {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
