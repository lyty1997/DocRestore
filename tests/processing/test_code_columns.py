# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""多栏切割单元测试（AGE-8 Phase 1.2）"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from docrestore.processing.code_columns import (
    Column,
    ColumnsResult,
    split_columns,
)


# ---------- 合成图工具 ----------

def _inject_noise(
    arr: np.ndarray,
    rng: np.random.Generator,
    density: float,
    low: int,
    high: int,
) -> None:
    mask = rng.random(arr.shape) < density
    count = int(mask.sum())
    if count:
        arr[mask] = rng.integers(low, high, size=count, dtype=np.uint8)


def _make_single_column(width: int = 1200, height: int = 900) -> Image.Image:
    """单栏代码区：全是代码噪点，无中央分隔条"""
    rng = np.random.default_rng(1)
    arr = np.full((height, width), 45, dtype=np.uint8)
    _inject_noise(arr, rng, density=0.35, low=180, high=241)
    return Image.fromarray(arr, mode="L").convert("RGB")


def _make_double_column(
    width: int = 1600,
    height: int = 900,
    sep_start: int = 790,
    sep_width: int = 8,
    sep_value: int = 30,
) -> Image.Image:
    """双栏代码区：中央 sep_width 像素是纯暗分隔条，两侧都是代码噪点"""
    rng = np.random.default_rng(2)
    arr = np.full((height, width), 45, dtype=np.uint8)
    # 左栏内容
    _inject_noise(arr[:, :sep_start], rng, density=0.35, low=180, high=241)
    # 分隔条
    arr[:, sep_start:sep_start + sep_width] = sep_value
    # 右栏内容
    _inject_noise(arr[:, sep_start + sep_width:], rng, density=0.35, low=180, high=241)
    return Image.fromarray(arr, mode="L").convert("RGB")


# ---------- 测试 ----------

class TestSingleColumn:
    def test_returns_one_column(self) -> None:
        image = _make_single_column()
        result = split_columns(image)
        assert isinstance(result, ColumnsResult)
        assert len(result.columns) == 1
        assert not result.detected_split
        assert not result.flags

    def test_column_image_same_size_as_input(self) -> None:
        image = _make_single_column(width=1200, height=900)
        result = split_columns(image)
        col = result.columns[0]
        assert col.column_index == 0
        assert col.image.size == (1200, 900)
        assert col.bbox == (0, 0, 1200, 900)


class TestDoubleColumn:
    def test_detects_central_separator(self) -> None:
        image = _make_double_column()
        result = split_columns(image)
        assert len(result.columns) == 2
        assert result.detected_split
        assert not result.flags

    def test_columns_bboxes_non_overlapping(self) -> None:
        image = _make_double_column(width=1600, sep_start=790, sep_width=8)
        result = split_columns(image)
        left, right = result.columns
        assert left.column_index == 0
        assert right.column_index == 1
        # 左栏 right 边 ≤ 右栏 left 边（不重叠）
        assert left.bbox[2] <= right.bbox[0]
        # 左栏右边界 ≈ sep_start（790 ± 5）
        assert 785 <= left.bbox[2] <= 795
        # 右栏左边界 ≈ sep_start + sep_width（798 ± 5）
        assert 793 <= right.bbox[0] <= 803

    def test_cropped_images_match_bbox(self) -> None:
        image = _make_double_column()
        result = split_columns(image)
        for col in result.columns:
            left, top, right, bottom = col.bbox
            assert col.image.size == (right - left, bottom - top)


class TestFallback:
    def test_wide_aspect_without_separator_falls_back_to_5050(self) -> None:
        """宽高比够大但没分隔条 → 硬切 50/50 + 打 flag"""
        # 2000x900 = aspect 2.22，超过 fallback_aspect_ratio=1.6
        image = _make_single_column(width=2000, height=900)
        result = split_columns(image)
        assert len(result.columns) == 2
        assert not result.detected_split
        assert "code.column_fallback_split" in result.flags
        # 50/50 切
        assert result.columns[0].bbox[2] == 1000
        assert result.columns[1].bbox[0] == 1000

    def test_normal_aspect_without_separator_single_column(self) -> None:
        """宽高比正常（非双屏）且没分隔条 → 单栏"""
        image = _make_single_column(width=1200, height=900)   # 1.33
        result = split_columns(image)
        assert len(result.columns) == 1


class TestEdgeCases:
    def test_tiny_image_raises(self) -> None:
        image = Image.new("RGB", (50, 50), (0, 0, 0))
        with pytest.raises(ValueError, match="too small"):
            split_columns(image)

    def test_separator_off_center_rejected(self) -> None:
        """靠边 10% 位置的分隔条不在 search_range 内 → 不切"""
        # 1300x900 aspect=1.44，低于 fallback_aspect_ratio=1.6
        image = _make_double_column(width=1300, height=900, sep_start=80, sep_width=8)
        result = split_columns(image)
        # 分隔条位于 6.2%，低于 20% 下限 → 不检出
        assert len(result.columns) == 1
        assert not result.detected_split

    def test_separator_too_wide_rejected(self) -> None:
        """分隔条过宽（50px） → 视为区域而非分隔，不检出"""
        image = _make_double_column(
            width=1300, height=900, sep_start=625, sep_width=50,
        )
        result = split_columns(image)
        assert len(result.columns) == 1
        assert not result.detected_split


# ---------- spike 真实照片 smoke ----------

SPIKE_DIR = Path(__file__).resolve().parents[2] / "test_images" / "age8-spike"


def _list_spike_images() -> list[Path]:
    if not SPIKE_DIR.exists():
        return []
    return sorted(
        p for p in SPIKE_DIR.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )


@pytest.mark.skipif(not _list_spike_images(), reason="age8-spike 测试图片不存在")
class TestSpikeImages:
    def test_does_not_crash_on_spike(self) -> None:
        """spike 真图不崩溃，返回合法 ColumnsResult（栏数 1 或 2）"""
        img = Image.open(_list_spike_images()[0]).convert("RGB")
        result = split_columns(img)
        assert 1 <= len(result.columns) <= 2
        for col in result.columns:
            assert isinstance(col, Column)
            assert col.image.size[0] > 0
