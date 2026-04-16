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

"""preprocessor 纯函数单元测试

覆盖 tile 网格计算与动态裁切：
- `_find_closest_aspect_ratio`：匹配最接近宽高比，面积启发式
- `_count_tiles`：返回合法 (i, j) 使 min_crops ≤ i*j ≤ max_crops
- `_dynamic_preprocess`：tile 数量 = ratio 乘积，每个 tile 尺寸正确

ImagePreprocessor 整体测试依赖 AutoTokenizer（模型权重），留给集成测试。
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip(
    "torch",
    reason="torch 未安装（仅 OCR extra 需要）",
)

from PIL import Image  # noqa: E402

from docrestore.ocr.preprocessor import (  # noqa: E402
    _count_tiles,
    _dynamic_preprocess,
    _find_closest_aspect_ratio,
)


class TestFindClosestAspectRatio:
    """_find_closest_aspect_ratio 选择最接近的 ratio"""

    def test_exact_match_returns_that_ratio(self) -> None:
        """给定候选 (1,1)(2,1)(1,2)，输入 aspect=2 → 应选 (2,1)。"""
        ratios = [(1, 1), (2, 1), (1, 2)]
        result = _find_closest_aspect_ratio(
            aspect_ratio=2.0,
            target_ratios=ratios,
            width=2000,
            height=1000,
            image_size=1000,
        )
        assert result == (2, 1)

    def test_tall_image_prefers_tall_ratio(self) -> None:
        ratios = [(1, 1), (2, 1), (1, 2)]
        result = _find_closest_aspect_ratio(
            aspect_ratio=0.5,
            target_ratios=ratios,
            width=500,
            height=1000,
            image_size=500,
        )
        assert result == (1, 2)

    def test_square_image_prefers_1x1(self) -> None:
        ratios = [(1, 1), (2, 1), (1, 2)]
        result = _find_closest_aspect_ratio(
            aspect_ratio=1.0,
            target_ratios=ratios,
            width=1000,
            height=1000,
            image_size=1000,
        )
        assert result == (1, 1)


class TestCountTiles:
    """_count_tiles 在 min/max 范围内选最佳 grid"""

    def test_respects_min_max_bounds(self) -> None:
        """返回的 i*j 必须在 [min_crops, max_crops] 内。"""
        for orig_w, orig_h in [
            (1024, 1024),
            (2048, 1024),
            (1024, 2048),
            (3000, 500),
        ]:
            i, j = _count_tiles(
                orig_width=orig_w,
                orig_height=orig_h,
                min_crops=2,
                max_crops=6,
                image_size=768,
            )
            assert 2 <= i * j <= 6, f"({orig_w}x{orig_h}) → ({i},{j})"

    def test_wide_image_prefers_wide_grid(self) -> None:
        """4:1 图片 → 宽方向应比高方向多 tile。"""
        i, j = _count_tiles(
            orig_width=4000,
            orig_height=1000,
            min_crops=2,
            max_crops=6,
            image_size=768,
        )
        assert i >= j

    def test_tall_image_prefers_tall_grid(self) -> None:
        """1:4 图片 → 高方向应比宽方向多 tile。"""
        i, j = _count_tiles(
            orig_width=1000,
            orig_height=4000,
            min_crops=2,
            max_crops=6,
            image_size=768,
        )
        assert j >= i


class TestDynamicPreprocess:
    """_dynamic_preprocess 产出正确的 tile 列表"""

    def test_tile_count_matches_ratio(self) -> None:
        """tiles 数量 = ratio[0] * ratio[1]。"""
        img = Image.new("RGB", (2048, 1024), color=(128, 128, 128))
        tiles, ratio = _dynamic_preprocess(
            img, min_crops=2, max_crops=6, image_size=768,
        )
        assert len(tiles) == ratio[0] * ratio[1]

    def test_every_tile_has_image_size(self) -> None:
        """每个 tile 尺寸都应是 image_size × image_size。"""
        img = Image.new("RGB", (1600, 800))
        tiles, _ = _dynamic_preprocess(
            img, min_crops=2, max_crops=6, image_size=512,
        )
        for t in tiles:
            assert t.size == (512, 512)

    def test_returns_pil_image_list(self) -> None:
        img = Image.new("RGB", (800, 800))
        tiles, _ = _dynamic_preprocess(
            img, min_crops=2, max_crops=4, image_size=400,
        )
        assert all(isinstance(t, Image.Image) for t in tiles)

    def test_ratio_within_crop_range(self) -> None:
        """_count_tiles 返回的 ratio 乘积应在 min/max 区间内。"""
        img = Image.new("RGB", (3000, 1000))
        _, ratio = _dynamic_preprocess(
            img, min_crops=2, max_crops=6, image_size=768,
        )
        assert 2 <= ratio[0] * ratio[1] <= 6
