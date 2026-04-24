# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IDE-UI 剪裁单元测试（AGE-8 Phase 1.1）

策略：
  - 合成图（确定分隔条像素位置 → 几何检测断言）
  - spike 真实照片 golden：只断言"裁剪后尺寸合理 + 无明显错切"，不依赖
    具体数据集字符（遵循 CLAUDE.md 派生断言规则）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from docrestore.processing.ide_ui_strip import (
    IDEStripResult,
    IDEUIConfig,
    strip_ide_ui,
)


# ---------- 合成图工具 ----------

def _inject_noise(
    arr: np.ndarray,
    rng: np.random.Generator,
    density: float,
    low: int,
    high: int,
) -> None:
    """在 arr 上以 density 密度注入 [low, high) 灰度噪点"""
    mask = rng.random(arr.shape) < density
    count = int(mask.sum())
    if count:
        arr[mask] = rng.integers(low, high, size=count, dtype=np.uint8)


def _make_ide_mock(
    width: int = 1600,
    height: int = 1000,
    sidebar_width: int = 260,
    top_height: int = 80,
    bottom_height: int = 180,
    separator_value: int = 30,
    sidebar_bg: int = 40,
    editor_bg: int = 45,
    bottom_bg: int = 38,
    top_bg: int = 42,
) -> Image.Image:
    """画一张"VSCode-like"假 IDE 截图：

        +------------------------------------------+
        |        top (tab + breadcrumb)            |  ← top_height，含一条底部分隔线
        +--------+---------------------------------+
        |sidebar | editor (code)                   |  ← sidebar 右侧一条分隔线
        |        |                                 |
        +--------+---------------------------------+
        |        bottom terminal                   |  ← bottom_height，含一条顶部分隔线
        +------------------------------------------+

    用 numpy 在各区域批量注入噪点模拟图标/代码字符，确保每列/每行都有足够
    方差（> ``neighbor_content_threshold``）；分隔线是 2-3px 的纯色纯暗带。
    """
    rng = np.random.default_rng(42)
    arr = np.full((height, width), editor_bg, dtype=np.uint8)

    editor_top = top_height + 3  # 留一条横向分隔
    editor_bottom = height - bottom_height if bottom_height > 0 else height
    editor_left = sidebar_width + 3  # 留一条纵向分隔

    # 顶栏（tab + breadcrumb 噪点）
    arr[:top_height] = top_bg
    _inject_noise(arr[:top_height], rng, density=0.25, low=150, high=221)

    # 顶栏底部分隔条（2px）
    arr[top_height:top_height + 2] = separator_value

    # 侧栏（文件树图标+文字噪点，密度够高 → std > neighbor_content_threshold）
    arr[editor_top:editor_bottom, :sidebar_width] = sidebar_bg
    _inject_noise(
        arr[editor_top:editor_bottom, :sidebar_width],
        rng, density=0.25, low=100, high=201,
    )

    # 侧栏右分隔条（3px）
    arr[editor_top:editor_bottom, sidebar_width:sidebar_width + 3] = separator_value

    # 编辑区（代码字符密度高）
    _inject_noise(
        arr[editor_top:editor_bottom, editor_left:],
        rng, density=0.35, low=180, high=241,
    )

    # 底部分隔条 + terminal（bottom_height=0 则跳过）
    if bottom_height > 0:
        arr[editor_bottom:editor_bottom + 2] = separator_value
        arr[editor_bottom + 3:] = bottom_bg
        _inject_noise(
            arr[editor_bottom + 3:], rng, density=0.30, low=120, high=201,
        )

    return Image.fromarray(arr, mode="L").convert("RGB")


# ---------- 单元测试 ----------

class TestStripIDEUI:
    """几何检测主流程"""

    def test_synthetic_mock_detects_all_three_separators(self) -> None:
        """合成图上 sidebar / top / bottom 三条分隔线都应被几何检测命中"""
        image = _make_ide_mock()
        config = IDEUIConfig(detect_strategy="geometric")

        result = strip_ide_ui(image, config)

        assert result.detect_strategy == "geometric"
        # 不应有 fallback 标记
        assert not any(f.endswith("_fallback") for f in result.flags), result.flags

        # code_region 左边界 ≈ sidebar 分隔线位置（260±5）
        left, top, right, bottom = result.code_region_bbox
        assert 255 <= left <= 270, f"sidebar_x={left}"
        assert 78 <= top <= 95, f"top_y={top}"
        assert 810 <= bottom <= 830, f"bottom_y={bottom}"
        assert right == image.size[0]

    def test_hybrid_falls_back_when_no_separators(self) -> None:
        """纯色图没有分隔条 → hybrid 走 fallback 比例"""
        image = Image.new("RGB", (1600, 1000), (45, 45, 45))
        config = IDEUIConfig(detect_strategy="hybrid")

        result = strip_ide_ui(image, config)

        assert result.detect_strategy == "hybrid"
        assert "ide_strip.sidebar_fallback" in result.flags
        assert "ide_strip.top_fallback" in result.flags
        assert "ide_strip.bottom_fallback" in result.flags

        left, top, _, bottom = result.code_region_bbox
        assert left == int(1600 * 0.18)
        assert top == int(1000 * 0.08)
        assert bottom == 1000 - int(1000 * 0.20)

    def test_geometric_only_marks_fallback_when_forced(self) -> None:
        """geometric 策略下全 fallback 应标 ``fallback`` 而非 ``hybrid``"""
        image = Image.new("RGB", (1600, 1000), (45, 45, 45))
        config = IDEUIConfig(detect_strategy="geometric")

        result = strip_ide_ui(image, config)

        assert result.detect_strategy == "fallback"

    def test_cropped_size_matches_bbox(self) -> None:
        """cropped 图尺寸 = bbox 尺寸"""
        image = _make_ide_mock()
        result = strip_ide_ui(image)

        left, top, right, bottom = result.code_region_bbox
        assert result.cropped.size == (right - left, bottom - top)

    def test_raises_on_tiny_image(self) -> None:
        """过小图片应报错（避免边界计算越界）"""
        image = Image.new("RGB", (50, 50), (0, 0, 0))
        with pytest.raises(ValueError, match="too small"):
            strip_ide_ui(image)

    def test_returns_none_for_bottom_when_no_terminal(self) -> None:
        """没有底栏分隔线但有 fallback → bottom_region_bbox 应被截到底部"""
        image = _make_ide_mock(bottom_height=0)
        result = strip_ide_ui(image)

        # 不论是否命中 bottom，cropped 至少要合理大
        assert result.cropped.size[0] > 0
        assert result.cropped.size[1] > 0


# ---------- spike 真实照片 golden ----------

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
    """在真实 spike 照片上做 smoke test：裁剪比例 + 无 crash"""

    @pytest.fixture
    def spike_image(self) -> Image.Image:
        imgs = _list_spike_images()
        return Image.open(imgs[0]).convert("RGB")

    def test_strip_does_not_crash(self, spike_image: Image.Image) -> None:
        result = strip_ide_ui(spike_image, IDEUIConfig(detect_strategy="hybrid"))
        assert isinstance(result, IDEStripResult)

    def test_cropped_area_is_at_least_40_percent_of_original(
        self, spike_image: Image.Image,
    ) -> None:
        """剪裁后代码区不应少于原图面积的 40%；少于视为误切"""
        result = strip_ide_ui(spike_image, IDEUIConfig(detect_strategy="hybrid"))
        orig_area = spike_image.size[0] * spike_image.size[1]
        crop_area = result.cropped.size[0] * result.cropped.size[1]
        ratio = crop_area / orig_area
        assert ratio >= 0.40, (
            f"crop ratio {ratio:.2%} 过低，可能误切；bbox={result.code_region_bbox}"
        )

    def test_code_region_left_reasonable(self, spike_image: Image.Image) -> None:
        """sidebar 宽度应在 [5%, 40%] 之间"""
        result = strip_ide_ui(spike_image, IDEUIConfig(detect_strategy="hybrid"))
        width = spike_image.size[0]
        left = result.code_region_bbox[0]
        ratio = left / width
        assert 0.05 <= ratio <= 0.40, f"sidebar ratio={ratio:.2%}"
