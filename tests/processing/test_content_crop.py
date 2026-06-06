# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""content_crop 正文区左右边界检测单元测试。

用合成的「左导航 + 中正文 + 右大纲」三栏图验证算法排除侧栏；不依赖真实数据集路径。
cv2 缺失时整文件跳过（与 slide_rectify 测试一致）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

import cv2  # noqa: E402
from cv2.typing import MatLike  # noqa: E402

from docrestore.processing.content_crop import (  # noqa: E402
    _runs,
    compute_crop_box,
    crop_page,
    detect_content_lr,
)


def _lines(
    img: MatLike, x0: int, x1: int, y0: int, y1: int, gap: int, thick: int,
) -> None:
    """在 [x0,x1]×[y0,y1] 区域按 gap 画黑色横线，模拟一栏文字行。"""
    y = y0
    while y < y1:
        cv2.line(img, (x0, y), (x1, y), (40, 40, 40), thick)
        y += gap


def _make_three_column(w: int = 1280, h: int = 900) -> MatLike:
    """左导航[40,200] + 中正文[460,840] + 右大纲[1080,1240]，栏间宽沟壑。"""
    img = np.full((h, w, 3), 255, np.uint8)
    _lines(img, 40, 200, 80, 820, 30, 4)     # 左导航：窄列密集短行
    _lines(img, 460, 840, 100, 400, 36, 6)   # 中正文上半（宽行）
    _lines(img, 460, 780, 460, 800, 36, 6)   # 中正文下半（含段间空白）
    _lines(img, 1080, 1240, 90, 700, 32, 4)  # 右大纲：窄列短行
    return img


def _make_full_width(w: int = 1280, h: int = 900) -> MatLike:
    """无侧栏：正文铺满整宽（模拟已人工裁剪的图）。"""
    img = np.full((h, w, 3), 255, np.uint8)
    _lines(img, 60, 1220, 100, 800, 36, 6)
    return img


class TestRuns:
    """连续段提取工具。"""

    def test_basic(self) -> None:
        m = np.array([False, True, True, False, True], dtype=np.bool_)
        assert _runs(m) == [(1, 3), (4, 5)]

    def test_empty_and_full(self) -> None:
        assert _runs(np.zeros(5, dtype=np.bool_)) == []
        assert _runs(np.ones(4, dtype=np.bool_)) == [(0, 4)]


class TestDetectContentLR:
    """正文列左右边界检测。"""

    def test_three_column_excludes_sidebars(self) -> None:
        """三栏图：检测框排除左导航与右大纲，覆盖中正文。"""
        res = detect_content_lr(_make_three_column())
        assert res is not None
        x0, x1 = res
        assert 200 < x0 < 460   # 排除左导航(右缘200)，落在中正文左缘附近
        assert 840 < x1 < 1080  # 覆盖中正文(右缘840)，排除右大纲(左缘1080)

    def test_full_width_returns_near_full(self) -> None:
        """无侧栏图：检测框接近整宽（供 S2 判已裁剪→跳过）。"""
        res = detect_content_lr(_make_full_width())
        assert res is not None
        x0, x1 = res
        assert (x1 - x0) / 1280 > 0.9

    def test_empty_image_returns_none(self) -> None:
        assert detect_content_lr(np.zeros((0, 0, 3), dtype=np.uint8)) is None

    def test_blank_image_returns_none(self) -> None:
        """纯白无文字 → 无文本列 → None。"""
        assert detect_content_lr(np.full((400, 600, 3), 255, np.uint8)) is None


class TestComputeCropBox:
    """裁剪框 + 已裁剪 / 误检跳过判据。"""

    def test_three_column_returns_box(self) -> None:
        """有侧栏：返回排除侧栏的裁剪框。"""
        box = compute_crop_box(_make_three_column())
        assert box is not None
        x0, x1 = box
        assert x0 > 200
        assert x1 < 1080

    def test_full_width_skipped(self) -> None:
        """无侧栏（已裁剪 / 全屏）：框宽近整宽 → 跳过返回 None。"""
        assert compute_crop_box(_make_full_width()) is None

    def test_blank_skipped(self) -> None:
        """检测失败 → 跳过 None。"""
        assert compute_crop_box(np.full((400, 600, 3), 255, np.uint8)) is None


class TestCropPage:
    """crop_page 异步入口：裁剪落盘 / 失败 / 跳过都回退原图。"""

    @pytest.mark.asyncio
    async def test_three_column_crops(self, tmp_path: Path) -> None:
        """有侧栏：落盘裁剪图并返回其路径，裁剪图比原图窄。"""
        src = tmp_path / "doc.jpg"
        cv2.imwrite(str(src), _make_three_column())
        out = await crop_page(src, tmp_path, save_debug=False)
        assert out != src
        cropped = cv2.imread(str(out))
        assert cropped is not None
        assert cropped.shape[1] < 1280

    @pytest.mark.asyncio
    async def test_full_width_returns_original(self, tmp_path: Path) -> None:
        """无侧栏（已裁剪）：回退原图路径。"""
        src = tmp_path / "cropped.jpg"
        cv2.imwrite(str(src), _make_full_width())
        out = await crop_page(src, tmp_path, save_debug=False)
        assert out == src

    @pytest.mark.asyncio
    async def test_missing_file_returns_original(
        self, tmp_path: Path,
    ) -> None:
        """读图失败：回退原图路径。"""
        src = tmp_path / "nonexist.jpg"
        out = await crop_page(src, tmp_path, save_debug=False)
        assert out == src
