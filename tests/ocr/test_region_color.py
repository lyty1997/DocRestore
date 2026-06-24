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

"""区域取色算法单测（§11.1）：合成像素图断言前景 / 背景色 + 弃权守卫。

输入全合成（numpy 构造），断言从输入派生（不写死数据集标识符）。覆盖：亮底黑字 /
暗色模式深底浅字 / 彩字 → 取色正确；纯色块 / 低对比 / 过小 / 双色块 / 噪声图 → 弃权。
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from docrestore.ocr.region_color import (
    estimate_white_balance,
    sample_region_color,
)

Rgb = tuple[int, int, int]
#: 默认竖条（模拟文字笔画，占少数像素）：行 20..40，三段列各 20px 宽
_STROKE_COLS = ((10, 30), (50, 70), (90, 110))


def _canvas(h: int, w: int, bg: Rgb) -> NDArray[np.uint8]:
    """纯底色画布 ``(h, w, 3) uint8``。"""
    img = np.empty((h, w, 3), dtype=np.uint8)
    img[:] = bg
    return img


def _strokes(bg: Rgb, fg: Rgb) -> NDArray[np.uint8]:
    """80x200 画布 + 几道竖条模拟文字笔画（少数像素）。"""
    img = _canvas(80, 200, bg)
    for c0, c1 in _STROKE_COLS:
        img[20:40, c0:c1] = fg
    return img


def _sample_full(img: NDArray[np.uint8]) -> tuple[Rgb, Rgb] | None:
    """对整图区域采样（bbox = 全图）。"""
    h, w = img.shape[:2]
    return sample_region_color(img, (0, 0, w, h))


def _close(actual: Rgb, expected: Rgb, tol: int = 24) -> bool:
    """逐通道差 ≤ tol（量化 + 均值有少量偏移，容差断言）。"""
    return all(abs(a - e) <= tol for a, e in zip(actual, expected, strict=True))


def test_dark_text_on_white_bg() -> None:
    """亮底黑字：前景≈黑、背景≈白。"""
    bg, fg = (255, 255, 255), (0, 0, 0)
    result = _sample_full(_strokes(bg, fg))
    assert result is not None
    got_fg, got_bg = result
    assert _close(got_fg, fg)
    assert _close(got_bg, bg)


def test_light_text_on_dark_bg_dark_mode() -> None:
    """暗色模式深底浅字：面积判别→前景=少数(浅字)、背景=多数(深底)，不判反。"""
    bg, fg = (20, 30, 80), (240, 240, 250)
    result = _sample_full(_strokes(bg, fg))
    assert result is not None
    got_fg, got_bg = result
    assert _close(got_fg, fg)
    assert _close(got_bg, bg)


def test_colored_text_on_white() -> None:
    """彩字白底：前景≈红、背景≈白。"""
    bg, fg = (255, 255, 255), (200, 20, 20)
    result = _sample_full(_strokes(bg, fg))
    assert result is not None
    got_fg, got_bg = result
    assert _close(got_fg, fg)
    assert _close(got_bg, bg)


def test_solid_block_abstains() -> None:
    """纯色块无文字 → 弃权（无第二色 / 对比不足）。"""
    assert _sample_full(_canvas(80, 200, (120, 160, 200))) is None


def test_low_contrast_abstains() -> None:
    """前景背景对比不足（浅灰字浅白底）→ 弃权。"""
    assert _sample_full(_strokes((245, 245, 245), (225, 225, 225))) is None


def test_tiny_region_abstains() -> None:
    """区域过小 → 弃权。"""
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    assert sample_region_color(img, (0, 0, 4, 4)) is None


def test_two_color_block_abstains() -> None:
    """双色块 50/50（非「少量笔画 + 大片背景」）→ 弃权。"""
    img = np.empty((80, 200, 3), dtype=np.uint8)
    img[:, :100] = (200, 50, 50)
    img[:, 100:] = (50, 50, 200)
    assert _sample_full(img) is None


def test_noisy_region_abstains() -> None:
    """高方差噪声（像图非字，无主导背景）→ 弃权。"""
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, (80, 200, 3), dtype=np.uint8)
    assert _sample_full(img) is None


def test_bbox_out_of_bounds_clamped() -> None:
    """bbox 越界自动 clamp，仍能采到色（不崩溃）。"""
    bg, fg = (255, 255, 255), (0, 0, 0)
    result = sample_region_color(_strokes(bg, fg), (-50, -50, 9999, 9999))
    assert result is not None
    _, got_bg = result
    assert _close(got_bg, bg)


def test_white_balance_corrects_blue_cast_background() -> None:
    """偏蓝白平衡：本应白的底被拍成淡蓝 → 白平衡校正后背景还原近白。"""
    bg, fg = (180, 200, 255), (10, 30, 90)  # 淡蓝白底 + 深字
    img = _strokes(bg, fg)
    wb = estimate_white_balance(img)
    assert wb is not None
    raw = _sample_full(img)  # 不校正
    h, w = img.shape[:2]
    corrected = sample_region_color(img, (0, 0, w, h), wb)
    assert raw is not None
    assert corrected is not None
    _, raw_bg = raw
    _, corr_bg = corrected
    # 校正后背景更接近白（最暗通道显著提升）
    assert min(corr_bg) > min(raw_bg)
    assert min(corr_bg) >= 230


def test_white_balance_none_for_dark_image() -> None:
    """暗色图（无亮背景）→ 不校正（None，不强制白化暗色主题）。"""
    assert estimate_white_balance(_canvas(80, 200, (15, 20, 40))) is None
