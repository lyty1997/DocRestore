# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式按编辑器 column 裁剪增强后进行二次 OCR。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from docrestore.models import TextLine
from docrestore.processing.ide_layout import IDELayout

if TYPE_CHECKING:
    from docrestore.models import PageOCR
    from docrestore.ocr.base import OCREngine


@dataclass(frozen=True)
class ColumnOCRConfig:
    """代码 column 二次 OCR 裁剪/增强配置。"""

    enabled: bool = False
    scale: int = 2
    padding_px: int = 6
    contrast: float = 1.35
    sharpness: float = 1.4
    grayscale: bool = True


@dataclass(frozen=True)
class ColumnCrop:
    """单个 column crop 元数据。"""

    column_index: int
    bbox: tuple[int, int, int, int]
    path: Path
    scale: int


async def rerun_column_ocr(
    page: PageOCR,
    layout: IDELayout,
    ocr_engine: OCREngine,
    output_dir: Path,
    config: ColumnOCRConfig,
) -> IDELayout:
    """对 layout 的每个代码 column 裁剪增强后重跑 OCR，并回映射 bbox。

    返回新的 IDELayout：anchors/above/below/sidebar 沿用首轮 OCR，columns
    使用二次 OCR 的回映射结果；某个 crop 无行级输出时回退该栏原 columns。
    """
    if not config.enabled or not layout.anchors:
        return layout

    crop_dir = output_dir / ".code_column_ocr" / page.image_path.stem
    crop_dir.mkdir(parents=True, exist_ok=True)
    crops = create_column_crops(
        page.image_path,
        layout,
        page.image_size,
        crop_dir,
        config,
    )
    if not crops:
        return layout

    remapped_columns: list[list[TextLine]] = []
    flags = list(layout.flags)
    for crop, original_column in zip(crops, layout.columns, strict=True):
        crop_ocr = await ocr_engine.ocr(crop.path, crop_dir)
        if not crop_ocr.text_lines:
            remapped_columns.append(original_column)
            flags.append(f"code.column_ocr.empty=col{crop.column_index}")
            continue
        remapped = [
            remap_crop_text_line(line, crop.bbox, crop.scale)
            for line in crop_ocr.text_lines
        ]
        remapped.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
        remapped_columns.append(remapped)
        flags.append(f"code.column_ocr.applied=col{crop.column_index}")

    return IDELayout(
        anchors=layout.anchors,
        columns=remapped_columns,
        above_code=layout.above_code,
        below_code=layout.below_code,
        sidebar=layout.sidebar,
        other=layout.other,
        flags=flags,
    )


def create_column_crops(
    image_path: Path,
    layout: IDELayout,
    image_size: tuple[int, int],
    output_dir: Path,
    config: ColumnOCRConfig,
) -> list[ColumnCrop]:
    """创建增强后的 per-column crop 图片。"""
    boxes = compute_column_crop_boxes(layout, image_size, config.padding_px)
    crops: list[ColumnCrop] = []
    if not boxes:
        return crops

    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        for index, bbox in enumerate(boxes):
            crop = image.crop(bbox)
            crop = enhance_column_crop(crop, config)
            path = output_dir / f"column_{index}.png"
            crop.save(path)
            crops.append(ColumnCrop(
                column_index=index,
                bbox=bbox,
                path=path,
                scale=max(1, config.scale),
            ))
    return crops


def compute_column_crop_boxes(
    layout: IDELayout,
    image_size: tuple[int, int],
    padding_px: int,
) -> list[tuple[int, int, int, int]]:
    """基于行号锚点和 column OCR 行计算裁剪边界。"""
    if not layout.anchors:
        return []
    width, height = image_size
    boxes: list[tuple[int, int, int, int]] = []
    for index, anchor in enumerate(layout.anchors):
        col_lines = layout.columns[index] if index < len(layout.columns) else []
        x1 = anchor.x1_min
        if index + 1 < len(layout.anchors):
            x2 = layout.anchors[index + 1].x1_min - 1
        elif col_lines:
            x2 = max(line.bbox[2] for line in col_lines)
        else:
            x2 = width
        y1 = anchor.y_top
        y2 = anchor.y_bottom
        if col_lines:
            y1 = min(y1, *(line.bbox[1] for line in col_lines))
            y2 = max(y2, *(line.bbox[3] for line in col_lines))
        box = _clamp_bbox(
            (
                x1 - padding_px,
                y1 - padding_px,
                x2 + padding_px,
                y2 + padding_px,
            ),
            image_size,
        )
        if box[2] > box[0] and box[3] > box[1]:
            boxes.append(box)
    return boxes


def enhance_column_crop(
    image: Image.Image,
    config: ColumnOCRConfig,
) -> Image.Image:
    """灰度、对比度、锐化、放大，提升小字号代码 OCR 可读性。"""
    out = image
    if config.grayscale:
        out = ImageOps.grayscale(out)
        out = ImageOps.autocontrast(out)
    if config.contrast != 1.0:
        out = ImageEnhance.Contrast(out).enhance(config.contrast)
    if config.sharpness != 1.0:
        out = ImageEnhance.Sharpness(out).enhance(config.sharpness)
    out = out.filter(ImageFilter.SHARPEN)
    scale = max(1, config.scale)
    if scale > 1:
        out = out.resize(
            (out.width * scale, out.height * scale),
            Image.Resampling.LANCZOS,
        )
    return out


def remap_crop_text_line(
    line: TextLine,
    crop_bbox: tuple[int, int, int, int],
    scale: int,
) -> TextLine:
    """把 crop OCR 的 bbox 映射回原图坐标系。"""
    factor = max(1, scale)
    x1, y1, x2, y2 = line.bbox
    crop_x1, crop_y1, _, _ = crop_bbox
    return TextLine(
        bbox=(
            crop_x1 + round(x1 / factor),
            crop_y1 + round(y1 / factor),
            crop_x1 + round(x2 / factor),
            crop_y1 + round(y2 / factor),
        ),
        text=line.text,
        score=line.score,
    )


def _clamp_bbox(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = image_size
    x1, y1, x2, y2 = bbox
    return (
        max(0, min(width, x1)),
        max(0, min(height, y1)),
        max(0, min(width, x2)),
        max(0, min(height, y2)),
    )
