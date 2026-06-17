#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""把 benchmark 输入统一渲染成逐页 PNG。

两类输入收敛到同一种「逐页 PNG」契约，保证 MinerU 与 PaddleOCR-VL 吃完全
相同的像素输入、且天然逐页对齐：
  - 公式 PDF：pypdfium2 按 DPI 渲染每页 → p{NN}.png
  - 屏摄照片：EXIF 转正 + 限长边降采样 → 原名.png

用法（在带 pypdfium2 的环境里跑，例如 mineru env）：
    conda run -n mineru python scripts/bench_quality/render_inputs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_ROOT = PROJECT_ROOT / "output" / "bench" / "quality" / "inputs"

PDF_DPI = 200
PHOTO_MAX_SIDE = 2200  # 屏摄原图过大时限长边，控制后续 judge 图片体积

# 公式密集学术 PDF（MinerU 主场）
FORMULA_PDF = (
    PROJECT_ROOT / "vendor" / "DeepSeek-OCR-2" / "DeepSeek_OCR2_paper.pdf"
)
# 屏摄照片（DocRestore 产品主场）：test_images 顶层 jpg
PHOTO_DIR = PROJECT_ROOT / "test_images"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}


def render_pdf(pdf_path: Path, out_dir: Path, dpi: int) -> int:
    """把 PDF 逐页渲染成 PNG，返回页数。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        n = len(doc)
        scale = dpi / 72.0
        for i in range(n):
            page = doc[i]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            dst = out_dir / f"p{i:02d}.png"
            image.save(dst)
            print(f"  PDF p{i:02d} -> {dst.name} {image.size}", flush=True)
        return n
    finally:
        doc.close()


def stage_photos(src_dir: Path, out_dir: Path, max_side: int) -> int:
    """把屏摄照片 EXIF 转正 + 限长边降采样写成 PNG，返回张数。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    photos = sorted(
        p for p in src_dir.iterdir()
        if p.is_file() and p.suffix in PHOTO_EXTS
    )
    for p in photos:
        with Image.open(p) as src:
            oriented = ImageOps.exif_transpose(src) or src
            im = (
                oriented.convert("RGB")
                if oriented.mode != "RGB"
                else oriented
            )
            long_side = max(im.size)
            if long_side > max_side:
                ratio = max_side / long_side
                new_size = (
                    round(im.size[0] * ratio),
                    round(im.size[1] * ratio),
                )
                im = im.resize(new_size, Image.Resampling.LANCZOS)
            dst = out_dir / f"{p.stem}.png"
            im.save(dst)
            print(f"  photo {p.name} -> {dst.name} {im.size}", flush=True)
    return len(photos)


def main() -> None:
    """渲染两类输入到 output/bench/quality/inputs/{formula_pdf,photos}。"""
    if not FORMULA_PDF.is_file():
        print(f"公式 PDF 不存在: {FORMULA_PDF}", file=sys.stderr)
        sys.exit(1)
    if not PHOTO_DIR.is_dir():
        print(f"屏摄目录不存在: {PHOTO_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"[formula_pdf] 渲染 {FORMULA_PDF.name} @ {PDF_DPI}dpi")
    n_pdf = render_pdf(FORMULA_PDF, OUT_ROOT / "formula_pdf", PDF_DPI)
    print(f"[photos] 处理 {PHOTO_DIR} 顶层照片")
    n_photo = stage_photos(PHOTO_DIR, OUT_ROOT / "photos", PHOTO_MAX_SIDE)

    print(f"\n✔ 渲染完成：公式 PDF {n_pdf} 页 + 屏摄照片 {n_photo} 张")
    print(f"  输出根目录：{OUT_ROOT}")


if __name__ == "__main__":
    main()
