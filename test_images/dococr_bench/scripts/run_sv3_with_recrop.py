#!/usr/bin/env python
"""sv3-light 端到端：原图 → 检测主列 → 裁剪 → 重跑。

步骤：
  1. 第一次 sv3-light(原图)，从 parsing_res_list 拿 bbox
  2. 把 bbox 归一化到 0..999，喂给 docrestore.ocr.column_filter.ColumnFilter
  3. 拿到 left_boundary / right_boundary，在原图上裁剪
  4. 第二次 sv3-light(裁剪图)，输出 markdown

输出每张图的总耗时 + 各阶段耗时 + 是否触发裁剪。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 让我们能 import docrestore 包
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))


def collect_bboxes_from_json(
    parsing_res: list, image_size: tuple[int, int],
) -> list[dict]:
    """从 sv3 输出的 parsing_res_list 解析 bbox 并归一化到 0..999。"""
    width, height = image_size
    if width == 0 or height == 0:
        return []

    out: list[dict] = []
    for item in parsing_res or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("block_label", "text"))
        bbox = item.get("block_bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = bbox
        out.append({
            "label": label,
            "x1": int(x1 * 999 / width),
            "y1": int(y1 * 999 / height),
            "x2": int(x2 * 999 / width),
            "y2": int(y2 * 999 / height),
            "text": str(item.get("block_content", "")),
        })
    return out


def detect_main_column(
    norm_boxes: list[dict],
) -> tuple[int, int, bool]:
    """用 docrestore.ColumnFilter 检测主列边界，返回 (left, right, has_sidebar)。"""
    from docrestore.ocr.column_filter import (
        ColumnFilter,
        GroundingRegion,
    )
    from docrestore.pipeline.config import ColumnFilterThresholds

    f = ColumnFilter(
        min_sidebar_count=5,
        thresholds=ColumnFilterThresholds(),
    )
    regions = [
        GroundingRegion(
            label=b["label"], x1=b["x1"], y1=b["y1"],
            x2=b["x2"], y2=b["y2"], text=b["text"], raw_block="",
        )
        for b in norm_boxes
    ]
    boundaries = f.detect_boundaries(regions)
    return (
        boundaries.left_boundary,
        boundaries.right_boundary,
        boundaries.has_sidebar,
    )


def run_one(
    pipeline,
    img_path: Path,
    out_root: Path,
    *,
    predict_kwargs: dict,
) -> dict:
    """单张完整流程。"""
    from PIL import Image

    case = img_path.parent.parent.name + "_" + img_path.parent.name
    out_dir = out_root / case / img_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: 全图 OCR
    t0 = time.perf_counter()
    output_pass1 = list(pipeline.predict(str(img_path), **predict_kwargs))
    pass1 = time.perf_counter() - t0

    parsing_res = []
    for res in output_pass1:
        try:
            data = res.json
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            parsing_res.extend(data.get("parsing_res_list", []) or [])
        except Exception:
            pass

    img = Image.open(img_path)
    img_size = img.size  # (W, H)

    norm_boxes = collect_bboxes_from_json(parsing_res, img_size)
    left, right, has_sidebar = detect_main_column(norm_boxes)

    # ── Step 2: 裁剪 + 重跑
    pass2 = 0.0
    used_path = img_path
    cropped = False
    if has_sidebar and (left > 0 or right < 999):
        cropped = True
        x1 = int(left * img_size[0] / 999)
        y1 = 0
        x2 = int(right * img_size[0] / 999)
        y2 = img_size[1]
        crop_img = img.crop((x1, y1, x2, y2))
        crop_path = out_dir / f"{img_path.stem}_main.jpg"
        crop_img.save(crop_path, quality=92)
        used_path = crop_path

        t0 = time.perf_counter()
        output_pass2 = list(pipeline.predict(str(crop_path), **predict_kwargs))
        pass2 = time.perf_counter() - t0
        for res in output_pass2:
            res.save_to_markdown(save_path=str(out_dir))
            try:
                res.save_to_img(save_path=str(out_dir))
            except Exception:
                pass
    else:
        for res in output_pass1:
            res.save_to_markdown(save_path=str(out_dir))
            try:
                res.save_to_img(save_path=str(out_dir))
            except Exception:
                pass

    return {
        "image": str(img_path),
        "pass1_s": pass1,
        "pass2_s": pass2,
        "total_s": pass1 + pass2,
        "cropped": cropped,
        "left": left,
        "right": right,
        "out_dir": str(out_dir),
        "used": str(used_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--images", nargs="+", required=True)
    args = parser.parse_args()

    from paddleocr import PPStructureV3

    init_t0 = time.perf_counter()
    pipeline = PPStructureV3(
        use_table_recognition=False,
        use_formula_recognition=False,
        use_chart_recognition=False,
        use_seal_recognition=False,
        use_region_detection=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
    )
    init_dt = time.perf_counter() - init_t0
    print(f"sv3-light init {init_dt:.2f}s", flush=True)

    predict_kwargs = {
        "text_det_limit_side_len": int(
            os.environ.get("OCR_DET_MAX_SIDE", "1600")
        ),
        "text_det_limit_type": "max",
    }

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    images = [Path(p) for p in args.images]
    # 暖机
    if images:
        _ = list(pipeline.predict(str(images[0]), **predict_kwargs))

    results = []
    for p in images:
        r = run_one(pipeline, p, out_root, predict_kwargs=predict_kwargs)
        results.append(r)
        print(
            f"  {p.name}: pass1={r['pass1_s']:.2f}s "
            f"pass2={r['pass2_s']:.2f}s total={r['total_s']:.2f}s "
            f"cropped={r['cropped']} L={r['left']} R={r['right']}",
            flush=True,
        )

    (out_root / "_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
    )
    print(f"\n报告: {out_root / '_report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
