#!/usr/bin/env python
"""sv3-light + DBSCAN 主区域检测端到端。

流程：
  1. PP-StructureV3 light 跑原图 → 取 overall_ocr_res.dt_polys（文本行级别）
  2. 多边形 → 轴对齐外接矩形
  3. main_region_detect.detect_main_region 做 DBSCAN，拿主区域 bbox
  4. 在原图上裁剪 → 第二次 sv3-light 输出最终 markdown

如果 DBSCAN 失败（无主簇），跳过裁剪，直接用第一次结果。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 让 main_region_detect 能 import
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def polys_to_bboxes(
    polys: list,
) -> list[tuple[float, float, float, float]]:
    """文本行多边形顶点列表 → 轴对齐外接矩形列表。"""
    out: list[tuple[float, float, float, float]] = []
    for p in polys:
        if not hasattr(p, "__iter__"):
            continue
        xs: list[float] = []
        ys: list[float] = []
        for pt in p:
            if not hasattr(pt, "__iter__"):
                continue
            seq = list(pt)
            if len(seq) < 2:
                continue
            xs.append(float(seq[0]))
            ys.append(float(seq[1]))
        if not xs or not ys:
            continue
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def collect_text_boxes(res_json) -> tuple[
    list[tuple[float, float, float, float]], tuple[int, int],
]:
    """从 sv3 res.json 拿文本行 bbox 与 (W, H)。"""
    top = res_json
    if isinstance(top, dict) and "res" in top and isinstance(
        top["res"], dict,
    ):
        top = top["res"]
    width = int(top.get("width") or 0)
    height = int(top.get("height") or 0)

    overall = top.get("overall_ocr_res") or {}
    polys = overall.get("dt_polys") or []
    bboxes = polys_to_bboxes(polys)
    return bboxes, (width, height)


def run_one(
    pipeline,
    img_path: Path,
    out_root: Path,
    *,
    predict_kwargs: dict,
) -> dict:
    from PIL import Image

    from main_region_detect import detect_main_region

    case = img_path.parent.parent.name + "_" + img_path.parent.name
    out_dir = out_root / case / img_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Pass 1: 全图
    t0 = time.perf_counter()
    output_pass1 = list(pipeline.predict(str(img_path), **predict_kwargs))
    pass1 = time.perf_counter() - t0

    # 拿文本行 bbox
    bboxes: list = []
    img_size = (0, 0)
    for res in output_pass1:
        try:
            data = res.json
        except Exception:
            continue
        boxes, sz = collect_text_boxes(data)
        if sz[0] > 0:
            img_size = sz
        bboxes.extend(boxes)

    # 兜底：如果 res.json 拿不到尺寸，从图像本身读
    if img_size == (0, 0):
        with Image.open(img_path) as im:
            img_size = im.size

    # DBSCAN 找主区域
    detect_t0 = time.perf_counter()
    region = detect_main_region(bboxes, img_size)
    detect_ms = (time.perf_counter() - detect_t0) * 1000

    # ── Pass 2: 裁剪重跑
    pass2 = 0.0
    cropped = False
    used_path = img_path
    if region.bbox is not None:
        cropped = True
        x1, y1, x2, y2 = region.bbox
        with Image.open(img_path) as im:
            crop_img = im.crop((x1, y1, x2, y2))
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
            try:
                res.save_to_json(save_path=str(out_dir))
            except Exception:
                pass
    else:
        for res in output_pass1:
            res.save_to_markdown(save_path=str(out_dir))
            try:
                res.save_to_img(save_path=str(out_dir))
            except Exception:
                pass
            try:
                res.save_to_json(save_path=str(out_dir))
            except Exception:
                pass

    return {
        "image": str(img_path),
        "pass1_s": pass1,
        "detect_ms": detect_ms,
        "pass2_s": pass2,
        "total_s": pass1 + pass2,
        "cropped": cropped,
        "bbox": list(region.bbox) if region.bbox else None,
        "n_total_boxes": region.n_total_boxes,
        "n_clusters": region.n_clusters,
        "main_cluster_size": region.main_cluster_size,
        "main_cluster_area_ratio": round(
            region.main_cluster_area_ratio, 4,
        ),
        "eps": round(region.eps, 1),
        "min_samples": region.min_samples,
        "line_height": round(region.line_height, 1),
        "image_size": list(img_size),
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
            os.environ.get("OCR_DET_MAX_SIDE", "1600"),
        ),
        "text_det_limit_type": "max",
    }

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    images = [Path(p) for p in args.images]
    if images:
        # 暖机
        _ = list(pipeline.predict(str(images[0]), **predict_kwargs))

    results = []
    for p in images:
        try:
            r = run_one(
                pipeline, p, out_root,
                predict_kwargs=predict_kwargs,
            )
        except Exception as exc:
            r = {"image": str(p), "error": str(exc)}
        results.append(r)
        if "error" in r:
            print(f"  {p.name}: ERROR {r['error']}", flush=True)
            continue
        print(
            f"  {p.name}: pass1={r['pass1_s']:.2f}s "
            f"det={r['detect_ms']:.0f}ms pass2={r['pass2_s']:.2f}s "
            f"total={r['total_s']:.2f}s "
            f"cropped={r['cropped']} bbox={r['bbox']} "
            f"clusters={r['n_clusters']}/main={r['main_cluster_size']}/"
            f"{r['n_total_boxes']} ratio={r['main_cluster_area_ratio']:.2f} "
            f"line_h={r['line_height']}",
            flush=True,
        )

    (out_root / "_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
    )
    print(f"\n报告: {out_root / '_report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
