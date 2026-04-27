#!/usr/bin/env python
"""屏幕拍摄文档的"主文档区域"轻量预裁剪。

思路：
  1. 缩放到 ~1024px 加速
  2. 灰度 → 自适应阈值（亮区=文档背景=白色）
  3. 形态学闭运算合并文字区
  4. 找最大连通域
  5. 取其外接矩形（含一定 padding）作为裁剪框
  6. 映射回原图坐标，crop 后写出

只用 OpenCV + numpy，无需 GPU。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np


def detect_doc_bbox(
    img_bgr: np.ndarray,
    *,
    work_max_side: int = 1024,
    min_area_ratio: float = 0.10,
    pad_ratio: float = 0.01,
) -> tuple[int, int, int, int] | None:
    """检测文档主区域边界框。返回 (x1, y1, x2, y2) 像素坐标，None 表示失败。"""
    h, w = img_bgr.shape[:2]
    scale = min(work_max_side / max(h, w), 1.0)
    if scale < 1.0:
        small = cv2.resize(
            img_bgr, (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = img_bgr.copy()

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # Otsu 阈值定位"亮"和"暗"
    _, bw = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    # 文档主体是大面积亮区。先做闭运算把文字"实心化"成块
    k = max(15, int(min(small.shape[:2]) * 0.01))
    if k % 2 == 0:
        k += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=2)

    # 取连通域
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        closed, connectivity=8,
    )
    if n_labels <= 1:
        return None

    img_area = small.shape[0] * small.shape[1]
    candidates: list[tuple[float, int, int, int, int]] = []
    for idx in range(1, n_labels):
        x, y, ww, hh, area = stats[idx]
        # 跳过紧贴边缘且面积小的（一般是噪声）
        if area < img_area * min_area_ratio:
            continue
        # 偏好接近矩形且尽量居中的连通域
        rect_fill = area / max(ww * hh, 1)
        # 中心距图像中心的偏移（小越好）
        cx_norm = (x + ww / 2) / small.shape[1]
        cy_norm = (y + hh / 2) / small.shape[0]
        center_score = 1.0 - max(
            abs(cx_norm - 0.5), abs(cy_norm - 0.5),
        ) * 2  # 0..1
        score = area * (rect_fill ** 0.5) * (0.5 + 0.5 * center_score)
        candidates.append((score, x, y, ww, hh))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    _, x, y, ww, hh = candidates[0]

    # 边距扩张
    pad = int(min(small.shape[:2]) * pad_ratio)
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(small.shape[1], x + ww + pad)
    y2 = min(small.shape[0], y + hh + pad)

    # 映射回原图
    inv = 1.0 / scale if scale < 1.0 else 1.0
    return (
        int(x1 * inv), int(y1 * inv),
        int(x2 * inv), int(y2 * inv),
    )


def process(
    src: Path, dst_dir: Path, *, draw_debug: bool = True,
) -> dict:
    img = cv2.imread(str(src))
    if img is None:
        return {"image": str(src), "ok": False, "error": "cv2.imread failed"}

    t0 = time.perf_counter()
    bbox = detect_doc_bbox(img)
    elapsed = time.perf_counter() - t0
    h, w = img.shape[:2]

    out: dict = {
        "image": str(src), "elapsed_ms": elapsed * 1000,
        "src_size": [w, h], "ok": bbox is not None,
    }

    dst_dir.mkdir(parents=True, exist_ok=True)
    if bbox is None:
        return out

    x1, y1, x2, y2 = bbox
    crop = img[y1:y2, x1:x2]
    crop_path = dst_dir / f"{src.stem}_main.jpg"
    cv2.imwrite(str(crop_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    out.update({
        "bbox": [x1, y1, x2, y2],
        "crop_size": [x2 - x1, y2 - y1],
        "crop_ratio": (x2 - x1) * (y2 - y1) / (w * h),
        "crop_path": str(crop_path),
    })

    if draw_debug:
        debug = img.copy()
        cv2.rectangle(debug, (x1, y1), (x2, y2), (0, 255, 0), 6)
        debug_path = dst_dir / f"{src.stem}_debug.jpg"
        # 缩到 1280 写出，省空间
        h, w = debug.shape[:2]
        scale = min(1280 / max(h, w), 1.0)
        if scale < 1.0:
            debug = cv2.resize(
                debug, (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_AREA,
            )
        cv2.imwrite(str(debug_path), debug, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--images", nargs="+", required=True)
    args = parser.parse_args()

    out_dir = Path(args.out)
    results = []
    for p in args.images:
        r = process(Path(p), out_dir)
        results.append(r)
        if r["ok"]:
            print(
                f"  {Path(p).name}: {r['elapsed_ms']:.1f}ms "
                f"bbox={r['bbox']} crop_ratio={r['crop_ratio']:.2%}",
                flush=True,
            )
        else:
            print(f"  {Path(p).name}: failed", flush=True)

    (out_dir / "_precrop_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
