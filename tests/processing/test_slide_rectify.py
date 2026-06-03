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

"""slide_rectify（S2 透视矫正）单测

核心算法用合成图做确定性断言；真图落盘用 test_images/PPT，断言从输入派生
（不写死数据集文件名）。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from docrestore.processing.slide_rectify import (
    ImageBGR,
    Quad,
    _order_corners,
    detect_slide_quad,
    rectify,
    rectify_page,
)

#: PPT 真图目录（issue 约定路径），相对项目根
_PPT_DIR = Path(__file__).resolve().parents[2] / "test_images" / "PPT"
#: 合成图里画的透视梯形 4 角（上窄下宽，模拟仰拍），顺序 tl tr br bl
_TRAPEZOID = [(200, 100), (600, 100), (700, 500), (100, 500)]


def _make_slide_image(
    corners: list[tuple[int, int]], size: tuple[int, int] = (600, 800),
) -> ImageBGR:
    """黑底上填白色四边形（模拟 LED 屏亮区），返回 BGR 图。"""
    h, w = size
    img = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.fillConvexPoly(img, np.array(corners, dtype=np.int32), (255, 255, 255))
    return img


def test_order_corners_sorts_to_tl_tr_br_bl() -> None:
    """打乱顺序的 4 点应被排成 左上 右上 右下 左下。"""
    shuffled = np.array(
        [(700, 500), (200, 100), (100, 500), (600, 100)], dtype=np.float32,
    )
    quad = _order_corners(shuffled)
    assert quad.top_left == (200, 100)
    assert quad.top_right == (600, 100)
    assert quad.bottom_right == (700, 500)
    assert quad.bottom_left == (100, 500)


def test_detect_finds_trapezoid() -> None:
    """合成透视梯形应被检测为四边形，4 角接近输入角（容差内）。"""
    img = _make_slide_image(_TRAPEZOID)
    quad = detect_slide_quad(img)
    assert quad is not None
    for got, want in zip(
        [quad.top_left, quad.top_right, quad.bottom_right, quad.bottom_left],
        _TRAPEZOID,
        strict=True,
    ):
        assert abs(got[0] - want[0]) <= 10
        assert abs(got[1] - want[1]) <= 10


def test_detect_returns_none_on_blank() -> None:
    """纯黑图（无亮区）应返回 None。"""
    blank = np.zeros((600, 800, 3), dtype=np.uint8)
    assert detect_slide_quad(blank) is None


def test_detect_returns_none_on_small_bright_blob() -> None:
    """小亮块（面积占比低于阈值）应被过滤，返回 None。"""
    img = _make_slide_image([(10, 10), (60, 10), (60, 60), (10, 60)])
    assert detect_slide_quad(img) is None


def test_rectify_outputs_positive_frontal() -> None:
    """给定四边形，矫正输出应为非空正视图。"""
    img = _make_slide_image(_TRAPEZOID)
    quad = Quad(
        top_left=(200, 100), top_right=(600, 100),
        bottom_right=(700, 500), bottom_left=(100, 500),
    )
    warped = rectify(img, quad, top_extend_ratio=0.2)
    assert warped.shape[0] > 0
    assert warped.shape[1] > 0


async def test_rectify_page_saves_before_after(tmp_path: Path) -> None:
    """合成梯形图矫正成功：返回 .rectified 下的 after 路径 + before/after 落盘。"""
    src = tmp_path / "slide.jpg"
    cv2.imwrite(str(src), _make_slide_image(_TRAPEZOID))
    out_dir = tmp_path / "out"
    result = await rectify_page(src, out_dir, save_debug=True)
    rectified = out_dir / ".rectified"
    assert result.parent == rectified
    assert (rectified / "slide_after.jpg").exists()
    assert (rectified / "slide_before.jpg").exists()


async def test_rectify_page_fallback_on_blank(tmp_path: Path) -> None:
    """检测不到四边形：回退原图路径，不落盘矫正图。"""
    src = tmp_path / "blank.jpg"
    cv2.imwrite(str(src), np.zeros((600, 800, 3), dtype=np.uint8))
    out_dir = tmp_path / "out"
    result = await rectify_page(src, out_dir)
    assert result == src
    assert not (out_dir / ".rectified").exists()


@pytest.mark.skipif(
    not _PPT_DIR.is_dir() or not any(_PPT_DIR.glob("*.jpg")),
    reason="无 test_images/PPT 真图",
)
async def test_rectify_page_on_real_ppt(tmp_path: Path) -> None:
    """真图：rectify_page 不崩、返回有效路径；矫正成功则证据从 stem 派生。"""
    src = sorted(_PPT_DIR.glob("*.jpg"))[0]
    out_dir = tmp_path / "out"
    result = await rectify_page(src, out_dir, save_debug=True)
    assert result.exists()
    rectified = out_dir / ".rectified"
    if result.parent == rectified:  # 矫正成功
        assert (rectified / f"{src.stem}_after{src.suffix}").exists()
        assert (rectified / f"{src.stem}_before{src.suffix}").exists()
    else:  # 检测失败回退原图
        assert result == src
