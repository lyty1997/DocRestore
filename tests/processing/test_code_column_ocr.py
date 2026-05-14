# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码 column 裁剪增强二次 OCR 测试。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from docrestore.models import PageOCR, TextLine
from docrestore.ocr.base import ProgressFn
from docrestore.processing.code_column_ocr import (
    ColumnOCRConfig,
    compute_column_crop_boxes,
    create_column_crops,
    remap_crop_text_line,
    rerun_column_ocr,
)
from docrestore.processing.ide_layout import IDELayout, LineNumberAnchor


def _anchor(
    x1: int,
    x2: int,
    *,
    y_top: int = 20,
    y_bottom: int = 140,
) -> LineNumberAnchor:
    return LineNumberAnchor(
        x1_center=x1,
        x1_min=x1,
        x2_max=x2,
        y_top=y_top,
        y_bottom=y_bottom,
        line_count=5,
        num_range=(1, 5),
        monotonic_ratio=1.0,
    )


def _layout() -> IDELayout:
    left_lines = [
        TextLine((30, 30, 45, 45), "1", 0.99),
        TextLine((70, 30, 170, 45), "left();", 0.98),
    ]
    right_lines = [
        TextLine((230, 30, 245, 45), "1", 0.99),
        TextLine((270, 30, 360, 45), "right();", 0.98),
    ]
    return IDELayout(
        anchors=[_anchor(30, 45), _anchor(230, 245)],
        columns=[left_lines, right_lines],
        above_code=[TextLine((30, 0, 200, 12), "tab", 0.9)],
        below_code=[],
        sidebar=[],
    )


class FakeColumnOCREngine:
    """返回 crop 坐标系下的行级 OCR。"""

    async def initialize(
        self, on_progress: ProgressFn | None = None,
    ) -> None:
        del on_progress

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        del output_dir
        if image_path.name == "column_0.png":
            text = "LEFT_FIXED();"
        else:
            text = "RIGHT_FIXED();"
        return PageOCR(
            image_path=image_path,
            image_size=(200, 100),
            raw_text=text,
            text_lines=[
                TextLine((80, 20, 180, 44), text, 0.99),
            ],
        )

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        results = []
        for index, path in enumerate(image_paths, start=1):
            results.append(await self.ocr(path, output_dir))
            if on_progress is not None:
                on_progress(index, len(image_paths))
        return results

    async def shutdown(self) -> None:
        return None

    @property
    def is_ready(self) -> bool:
        return True


def test_compute_column_crop_boxes_clamps_and_splits_columns() -> None:
    boxes = compute_column_crop_boxes(_layout(), (400, 200), padding_px=8)
    assert boxes == [
        (22, 12, 237, 148),
        (222, 12, 368, 148),
    ]


def test_create_column_crops_enhances_and_scales(tmp_path: Path) -> None:
    image_path = tmp_path / "page.jpg"
    Image.new("RGB", (400, 200), color=(30, 30, 30)).save(image_path)
    crops = create_column_crops(
        image_path,
        _layout(),
        (400, 200),
        tmp_path / "crops",
        ColumnOCRConfig(enabled=True, scale=2),
    )
    assert len(crops) == 2
    with Image.open(crops[0].path) as crop:
        assert crop.size == (
            (crops[0].bbox[2] - crops[0].bbox[0]) * 2,
            (crops[0].bbox[3] - crops[0].bbox[1]) * 2,
        )


def test_remap_crop_text_line_to_original_coordinates() -> None:
    line = TextLine((20, 10, 60, 30), "x", 0.9)
    remapped = remap_crop_text_line(line, (100, 200, 300, 400), scale=2)
    assert remapped.bbox == (110, 205, 130, 215)
    assert remapped.text == "x"


@pytest.mark.asyncio
async def test_rerun_column_ocr_remaps_dual_column_lines(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "page.jpg"
    Image.new("RGB", (400, 200), color=(30, 30, 30)).save(image_path)
    page = PageOCR(
        image_path=image_path,
        image_size=(400, 200),
        raw_text="",
        text_lines=[line for column in _layout().columns for line in column],
    )
    refined = await rerun_column_ocr(
        page,
        _layout(),
        FakeColumnOCREngine(),
        tmp_path / "out",
        ColumnOCRConfig(enabled=True, scale=2),
    )
    assert refined.columns[0][0].text == "LEFT_FIXED();"
    assert refined.columns[1][0].text == "RIGHT_FIXED();"
    assert refined.columns[0][0].bbox[0] >= 0
    assert refined.above_code[0].text == "tab"
    assert "code.column_ocr.applied=col0" in refined.flags
