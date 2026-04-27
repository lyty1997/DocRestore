#!/usr/bin/env python
"""PP-StructureV3 vs PaddleOCR vs PaddleOCRVL 性能/质量对比脚本

运行：在 ppocr_client conda 环境中执行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def warm_up_message(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def run_structurev3(
    images: list[Path],
    out_root: Path,
    *,
    light: str,
    label: str,
) -> dict:
    """跑 PP-StructureV3。light: "light" / "region" / "full"。"""
    from paddleocr import PPStructureV3

    warm_up_message(f"PP-StructureV3 ({label})")

    init_kwargs: dict = {}
    if light == "light":
        init_kwargs.update({
            "use_table_recognition": False,
            "use_formula_recognition": False,
            "use_chart_recognition": False,
            "use_seal_recognition": False,
            "use_region_detection": False,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
        })
    elif light == "region":
        # 启用 PP-DocBlockLayout 检测主文档区域，关闭重型识别器
        init_kwargs.update({
            "use_table_recognition": False,
            "use_formula_recognition": False,
            "use_chart_recognition": False,
            "use_seal_recognition": False,
            "use_region_detection": True,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
        })
    device = os.environ.get("PADDLE_DEVICE")
    if device:
        init_kwargs["device"] = device

    init_t0 = time.perf_counter()
    pipeline = PPStructureV3(**init_kwargs)
    init_dt = time.perf_counter() - init_t0
    print(f"[{label}] init {init_dt:.2f}s", flush=True)

    results: list[dict] = []
    # 限制 OCR 检测的最长边，避免大图爆显存
    predict_kwargs = {
        "text_det_limit_side_len": int(
            os.environ.get("OCR_DET_MAX_SIDE", "1600")
        ),
        "text_det_limit_type": "max",
    }
    # 暖机一次
    _ = list(pipeline.predict(str(images[0]), **predict_kwargs))

    for img in images:
        out_dir = out_root / label / img.parent.parent.name / img.parent.name
        out_dir.mkdir(parents=True, exist_ok=True)
        per_img_dir = out_dir / img.stem
        per_img_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.perf_counter()
        output = list(pipeline.predict(str(img), **predict_kwargs))
        elapsed = time.perf_counter() - t0

        for res in output:
            res.save_to_markdown(save_path=str(per_img_dir))
            try:
                res.save_to_json(save_path=str(per_img_dir))
            except Exception as exc:
                print(f"  json 保存失败: {exc}", flush=True)
            try:
                res.save_to_img(save_path=str(per_img_dir))
            except Exception as exc:
                print(f"  img 可视化失败: {exc}", flush=True)

        results.append({
            "image": str(img),
            "elapsed": elapsed,
            "out": str(per_img_dir),
        })
        print(f"  {img.name}: {elapsed:.2f}s -> {per_img_dir}",
              flush=True)

    return {"label": label, "init": init_dt, "results": results}


def run_paddleocr(
    images: list[Path],
    out_root: Path,
    *,
    label: str,
) -> dict:
    """纯 OCR：文本检测 + 识别。"""
    from paddleocr import PaddleOCR

    warm_up_message(f"PaddleOCR ({label})")

    init_kwargs: dict = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
    }
    device = os.environ.get("PADDLE_DEVICE")
    if device:
        init_kwargs["device"] = device

    init_t0 = time.perf_counter()
    pipeline = PaddleOCR(**init_kwargs)
    init_dt = time.perf_counter() - init_t0
    print(f"[{label}] init {init_dt:.2f}s", flush=True)

    predict_kwargs = {
        "text_det_limit_side_len": int(
            os.environ.get("OCR_DET_MAX_SIDE", "1600")
        ),
        "text_det_limit_type": "max",
    }
    # 暖机
    _ = list(pipeline.predict(str(images[0]), **predict_kwargs))

    results = []
    for img in images:
        out_dir = out_root / label / img.parent.parent.name / img.parent.name
        out_dir.mkdir(parents=True, exist_ok=True)
        per_img_dir = out_dir / img.stem
        per_img_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.perf_counter()
        output = list(pipeline.predict(str(img), **predict_kwargs))
        elapsed = time.perf_counter() - t0

        for res in output:
            try:
                res.save_to_json(save_path=str(per_img_dir))
            except Exception as exc:
                print(f"  json 保存失败: {exc}", flush=True)
            try:
                res.save_to_img(save_path=str(per_img_dir))
            except Exception as exc:
                print(f"  img 可视化失败: {exc}", flush=True)

        results.append({
            "image": str(img),
            "elapsed": elapsed,
            "out": str(per_img_dir),
        })
        print(f"  {img.name}: {elapsed:.2f}s -> {per_img_dir}",
              flush=True)

    return {"label": label, "init": init_dt, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--images", nargs="+", required=True,
                        help="图片绝对路径列表")
    parser.add_argument("--mode",
                        choices=["sv3-full", "sv3-light", "ocr-only",
                                 "sv3-region"],
                        required=True)
    parser.add_argument("--device", default=None,
                        help="paddle device，例如 gpu / cpu / gpu:0")
    args = parser.parse_args()
    if args.device:
        os.environ["PADDLE_DEVICE"] = args.device

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    images = [Path(p) for p in args.images]

    if args.mode == "sv3-full":
        report = run_structurev3(
            images, out_root, light="full", label="sv3-full",
        )
    elif args.mode == "sv3-light":
        report = run_structurev3(
            images, out_root, light="light", label="sv3-light",
        )
    elif args.mode == "sv3-region":
        report = run_structurev3(
            images, out_root, light="region", label="sv3-region",
        )
    else:
        report = run_paddleocr(images, out_root, label="ocr-only")

    report_path = out_root / f"_report_{args.mode}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n报告: {report_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
