#!/usr/bin/env python3
# Copyright 2026 @lyty1997

# mypy: ignore-errors
"""AGE-8 行号列锚点方案全量验证

扫给定目录的所有 JPG，跑 PP-OCRv5 行级 OCR + analyze_layout，
统计成功率/anchor 分布/单调性分布/quality flag 分布。

环境：ppocr_client conda 环境
用法：
    /home/lyty/work/ai/env/anaconda3/envs/ppocr_client/bin/python3 \\
        scripts/age8_validate_full_dataset.py \\
        --input /mnt/TrueNAS_Share/chromium/chromium_decode/code/ \\
        --output output/age8-validate-full \\
        [--limit 50]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 让 docrestore 包可以 import（ppocr_client 环境没装项目）
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


def _scan_images(input_dir: Path) -> list[Path]:
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )


def _ocr_to_lines(ocr, img_path: Path) -> tuple[list[dict], tuple[int, int]]:
    """跑一张图 OCR，返回 (rec_box+text+score 列表, image_size)"""
    from PIL import Image

    image_size = Image.open(img_path).size
    output = ocr.predict(str(img_path))
    lines: list[dict] = []
    for res in output:
        data = res.json
        inner = data.get("res", data) if isinstance(data, dict) else {}
        rec_boxes = inner.get("rec_boxes") or []
        rec_texts = inner.get("rec_texts") or []
        rec_scores = inner.get("rec_scores") or []
        for i, box in enumerate(rec_boxes):
            if len(box) < 4:
                continue
            x1, y1, x2, y2 = (int(v) for v in box[:4])
            lines.append({
                "bbox": [x1, y1, x2, y2],
                "text": rec_texts[i] if i < len(rec_texts) else "",
                "score": float(rec_scores[i]) if i < len(rec_scores) else 0.0,
            })
    return lines, image_size


def _analyze(lines_dict_list, image_size):
    """跑 ide_layout + code_assembly 完整链路"""
    from docrestore.models import TextLine
    from docrestore.processing.code_assembly import assemble_columns
    from docrestore.processing.ide_layout import analyze_layout

    text_lines = [
        TextLine(
            bbox=tuple(int(v) for v in ln["bbox"][:4]),
            text=ln["text"],
            score=float(ln["score"]),
        )
        for ln in lines_dict_list
    ]
    layout = analyze_layout(text_lines, image_size)
    columns = assemble_columns(layout)
    return layout, columns


def _summarize(per_image: list[dict]) -> dict:
    """全量统计"""
    total = len(per_image)
    if total == 0:
        return {}
    n_anchor = Counter(item["anchor_count"] for item in per_image)
    flag_counts = Counter()
    for item in per_image:
        for f in item["flags"]:
            flag_counts[f] += 1

    # 至少 1 个 anchor 的图 = 成功识别
    success = sum(1 for item in per_image if item["anchor_count"] >= 1)

    # 平均单调性
    mono_values = [
        item["max_monotonic"] for item in per_image
        if item["max_monotonic"] is not None
    ]
    avg_mono = sum(mono_values) / len(mono_values) if mono_values else 0.0

    # 单调性 ≥ 0.9 的图占比
    high_mono = sum(1 for v in mono_values if v >= 0.9)

    # 列数分布
    n_columns = Counter(item["anchor_count"] for item in per_image)

    return {
        "total": total,
        "success": success,
        "success_rate": round(success / total, 4),
        "anchor_count_distribution": dict(sorted(n_anchor.items())),
        "n_columns_distribution": dict(sorted(n_columns.items())),
        "avg_max_monotonic": round(avg_mono, 4),
        "high_monotonic_count_geq_0.9": high_mono,
        "high_monotonic_rate_geq_0.9": (
            round(high_mono / len(mono_values), 4) if mono_values else 0.0
        ),
        "flag_distribution": dict(flag_counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path, action="append", required=True,
        help="可重复，多个数据集",
    )
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "output" / "age8-validate-full",
    )
    parser.add_argument("--limit", type=int, default=0, help="0=全量")
    parser.add_argument(
        "--label", default="default",
        help="数据集标签，写入 per_image.dataset 字段",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    images: list[tuple[str, Path]] = []
    for in_dir in args.input:
        for p in _scan_images(in_dir):
            images.append((args.label, p))
    if args.limit:
        images = images[: args.limit]
    print(f"validating {len(images)} images")

    from paddleocr import PaddleOCR

    print("=== loading PaddleOCR ===")
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )

    per_image: list[dict] = []
    t_start = time.time()
    for idx, (label, img_path) in enumerate(images, 1):
        try:
            lines_data, image_size = _ocr_to_lines(ocr, img_path)
            layout, code_columns = _analyze(lines_data, image_size)
        except Exception as exc:
            print(f"[{idx:>3}/{len(images)}] {img_path.name}: ERROR {exc}")
            per_image.append({
                "dataset": label,
                "stem": img_path.stem,
                "error": str(exc),
                "anchor_count": 0,
                "max_monotonic": None,
                "line_count": 0,
                "flags": ["validate.ocr_failed"],
            })
            continue
        anchor_count = len(layout.anchors)
        max_mono = (
            max((a.monotonic_ratio for a in layout.anchors), default=None)
            if layout.anchors else None
        )
        column_lengths = [len(c) for c in layout.columns]
        per_image.append({
            "dataset": label,
            "stem": img_path.stem,
            "image_size": list(image_size),
            "line_count": len(lines_data),
            "anchor_count": anchor_count,
            "max_monotonic": max_mono,
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
            "column_lengths": column_lengths,
            "above_count": len(layout.above_code),
            "below_count": len(layout.below_code),
            "sidebar_count": len(layout.sidebar),
            "other_count": len(layout.other),
            "flags": list(layout.flags),
            # code_assembly 集成统计
            "assembled_columns": len(code_columns),
            "assembled_lines_per_col": [len(c.lines) for c in code_columns],
            "char_widths": [c.char_width for c in code_columns],
            "line_heights": [c.avg_line_height for c in code_columns],
            "total_line_gaps": sum(len(c.line_gaps) for c in code_columns),
            "assembly_flags": [
                f for c in code_columns for f in c.flags
            ],
        })
        if idx % 20 == 0 or idx == len(images):
            elapsed = time.time() - t_start
            avg = elapsed / idx
            remain = avg * (len(images) - idx)
            print(
                f"[{idx:>3}/{len(images)}] {img_path.name} "
                f"anchors={anchor_count} mono={max_mono} "
                f"flags={layout.flags}  "
                f"avg={avg:.2f}s eta={remain:.0f}s"
            )

    summary = _summarize(per_image)
    (args.output / "per_image.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in per_image),
        encoding="utf-8",
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print()
    print("=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
