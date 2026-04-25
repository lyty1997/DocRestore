#!/usr/bin/env python3
# Copyright 2026 @lyty1997

# mypy: ignore-errors
"""AGE-8 端到端内容验收：原图 → OCR → ide_layout → code_assembly → 代码文本

输出每张图的 code_text 到独立文件，配合原图肉眼逐行对照验证算法在
真实数据上的字符级输出质量（不止统计行数）。

环境：ppocr_client conda（含 paddleocr + paddle）
用法：
    /home/lyty/work/ai/env/anaconda3/envs/ppocr_client/bin/python3 \\
        scripts/age8_e2e_check.py [--stems DSC06835,DSC06836,...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

OUT_ROOT = PROJECT_ROOT / "output" / "age8-e2e"
SPIKE_DIR = PROJECT_ROOT / "test_images" / "age8-spike"
LINES_CACHE_DIR = PROJECT_ROOT / "output" / "age8-probe-basic"


def _load_or_run_ocr(stem: str):
    """优先读已有 lines.jsonl 缓存；不存在则跑 OCR。"""
    cached = LINES_CACHE_DIR / stem / "lines.jsonl"
    if cached.exists():
        items = [
            json.loads(line) for line in cached.read_text().splitlines()
            if line.strip()
        ]
        return items, "cached"

    from paddleocr import PaddleOCR
    from PIL import Image
    img = SPIKE_DIR / f"{stem}.JPG"
    print(f"  running OCR on {img}...")
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_box_thresh=0.3,
        text_det_unclip_ratio=1.8,
    )
    items = []
    for res in ocr.predict(str(img)):
        data = res.json
        inner = data.get("res", data) if isinstance(data, dict) else {}
        rec_boxes = inner.get("rec_boxes") or []
        rec_texts = inner.get("rec_texts") or []
        rec_scores = inner.get("rec_scores") or []
        for i, b in enumerate(rec_boxes):
            if len(b) < 4:
                continue
            items.append({
                "bbox": [int(v) for v in b[:4]],
                "text": rec_texts[i] if i < len(rec_texts) else "",
                "score": float(rec_scores[i]) if i < len(rec_scores) else 0.0,
            })
    return items, "fresh"


def _process(stem: str) -> dict:
    from PIL import Image

    from docrestore.models import TextLine
    from docrestore.processing.code_assembly import assemble_columns
    from docrestore.processing.ide_layout import analyze_layout

    items, src = _load_or_run_ocr(stem)
    img = SPIKE_DIR / f"{stem}.JPG"
    sz = Image.open(img).size

    text_lines = [
        TextLine(
            bbox=tuple(int(v) for v in it["bbox"][:4]),
            text=it["text"],
            score=float(it["score"]),
        )
        for it in items
    ]
    layout = analyze_layout(text_lines, sz)
    columns = assemble_columns(layout)

    out_dir = OUT_ROOT / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # 写每栏代码文本（关键产出）
    for col in columns:
        col_path = out_dir / f"column_{col.column_index}.txt"
        col_path.write_text(col.code_text, encoding="utf-8")

    # 写区域归类（让 hooman audit 各区是否正确）
    regions_path = out_dir / "regions.txt"
    regions_path.write_text("\n".join([
        f"=== above_code ({len(layout.above_code)} 行) ===",
        *(f"  | {ln.text}" for ln in layout.above_code),
        f"\n=== sidebar ({len(layout.sidebar)} 行) ===",
        *(f"  | {ln.text}" for ln in layout.sidebar),
        f"\n=== below_code ({len(layout.below_code)} 行) ===",
        *(f"  | {ln.text}" for ln in layout.below_code),
        f"\n=== other ({len(layout.other)} 行) ===",
        *(f"  | {ln.text}" for ln in layout.other),
    ]) + "\n", encoding="utf-8")

    # 元数据
    meta = {
        "stem": stem,
        "ocr_source": src,
        "image_size": list(sz),
        "anchors": [
            {
                "x1_center": a.x1_center,
                "y_top": a.y_top,
                "y_bottom": a.y_bottom,
                "line_count": a.line_count,
                "num_range": list(a.num_range),
                "monotonic_ratio": a.monotonic_ratio,
            }
            for a in layout.anchors
        ],
        "layout_flags": layout.flags,
        "columns": [
            {
                "index": col.column_index,
                "lines": len(col.lines),
                "char_width": col.char_width,
                "line_height": col.avg_line_height,
                "line_gaps": col.line_gaps,
                "flags": col.flags,
            }
            for col in columns
        ],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return meta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stems",
        default="DSC06835,DSC06836,DSC06838,DSC06840,DSC06841,DSC06842",
    )
    args = parser.parse_args()
    stems = [s.strip() for s in args.stems.split(",") if s.strip()]

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"=== AGE-8 端到端验收 ({len(stems)} 张) ===\n")
    for stem in stems:
        print(f">>> {stem}")
        meta = _process(stem)
        anchors = meta["anchors"]
        cols = meta["columns"]
        print(
            f"  anchors={len(anchors)} flags={meta['layout_flags']}  "
            f"columns={[(c['index'], c['lines'], c['flags']) for c in cols]}"
        )
    print(f"\n输出 → {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
