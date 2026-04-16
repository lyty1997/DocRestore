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

"""侧栏检测与过滤单元测试

纯 CPU 测试，不依赖 GPU / OCR 引擎。
从模拟的 grounding 数据构造测试用例。
"""

from __future__ import annotations

import pytest

from docrestore.ocr.column_filter import (
    ColumnBoundaries,
    ColumnFilter,
    GroundingRegion,
)


def _make_region(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    label: str = "text",
    text: str = "dummy",
) -> GroundingRegion:
    """构造测试用 GroundingRegion。"""
    raw_block = (
        f"<|ref|>{label}<|/ref|>"
        f"<|det|>[[{x1},{y1},{x2},{y2}]]<|/det|>"
        f"\n{text}"
    )
    return GroundingRegion(
        label=label,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        text=text,
        raw_block=raw_block,
    )


def _build_left_sidebar(count: int = 6) -> list[GroundingRegion]:
    """生成左栏导航区域（x1 < 100, x2 <= 220）。

    y 范围 120-820，模拟真实侧栏纵向跨越页面大部分区域。
    """
    step = max(1, 700 // max(count, 1))
    return [
        _make_region(
            10, 120 + i * step, 200, 140 + i * step,
            text=f"nav_item_{i}",
        )
        for i in range(count)
    ]


def _build_right_sidebar(count: int = 6) -> list[GroundingRegion]:
    """生成右栏大纲区域（x1 >= 800, width < 200）。

    y 范围 120-820，模拟真实侧栏纵向跨越页面大部分区域。
    """
    step = max(1, 700 // max(count, 1))
    return [
        _make_region(
            850, 120 + i * step, 990, 140 + i * step,
            text=f"toc_item_{i}",
        )
        for i in range(count)
    ]


def _build_content(count: int = 10) -> list[GroundingRegion]:
    """生成正文区域（x1 在 250-700 之间）。

    y 范围 120-920，模拟真实正文纵向跨越整个页面。
    """
    step = max(1, 800 // max(count, 1))
    return [
        _make_region(
            250, 120 + i * step, 750, 160 + i * step,
            text=f"content_line_{i}",
        )
        for i in range(count)
    ]


class TestDetectBoundaries:
    """detect_boundaries 测试"""

    def test_three_column_page(self) -> None:
        """三栏页面：检测到左右栏。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = (
            _build_left_sidebar(6)
            + _build_content(10)
            + _build_right_sidebar(6)
        )
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary > 0
        assert boundaries.right_boundary < 999

    def test_two_column_left_only(self) -> None:
        """双栏页面（只有左栏）。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = _build_left_sidebar(6) + _build_content(10)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary > 0
        assert boundaries.right_boundary == 999

    def test_two_column_right_only(self) -> None:
        """双栏页面（只有右栏）。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = _build_content(10) + _build_right_sidebar(6)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary == 0
        assert boundaries.right_boundary < 999

    def test_single_column_page(self) -> None:
        """单栏页面（纯正文）→ has_sidebar=False。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = _build_content(10)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is False
        assert boundaries.left_boundary == 0
        assert boundaries.right_boundary == 999

    def test_too_few_sidebar_regions(self) -> None:
        """侧栏区域不足 min_sidebar_count → 不触发。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = _build_left_sidebar(3) + _build_content(10)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is False

    def test_empty_regions(self) -> None:
        """空输入。"""
        cf = ColumnFilter()
        boundaries = cf.detect_boundaries([])

        assert boundaries.has_sidebar is False
        assert boundaries.left_boundary == 0
        assert boundaries.right_boundary == 999


class TestFilterRegions:
    """filter_regions 测试"""

    def test_filters_both_sidebars(self) -> None:
        """三栏页面过滤后只剩正文。"""
        cf = ColumnFilter(min_sidebar_count=5)
        left = _build_left_sidebar(6)
        right = _build_right_sidebar(6)
        content = _build_content(10)
        all_regions = left + content + right

        boundaries = cf.detect_boundaries(all_regions)
        filtered = cf.filter_regions(all_regions, boundaries)

        assert len(filtered) == len(content)
        # 所有保留区域的 x1 应在正文范围内
        for r in filtered:
            assert r.x1 >= 100 or r.x2 > 260

    def test_no_sidebar_no_filter(self) -> None:
        """无侧栏时不过滤。"""
        cf = ColumnFilter(min_sidebar_count=5)
        content = _build_content(10)
        boundaries = ColumnBoundaries()  # 默认无侧栏

        filtered = cf.filter_regions(content, boundaries)
        assert len(filtered) == len(content)


class TestRebuildText:
    """rebuild_text 测试"""

    def test_preserves_grounding_tags(self) -> None:
        """重建文本保留 grounding 标签格式。"""
        cf = ColumnFilter()
        regions = _build_content(3)
        rebuilt = cf.rebuild_text(regions)

        # 应包含 grounding 标签
        assert "<|ref|>" in rebuilt
        assert "<|det|>" in rebuilt
        # 应包含正文内容
        assert "content_line_0" in rebuilt
        assert "content_line_2" in rebuilt

    def test_round_trip_with_parse_grounding(self) -> None:
        """rebuild 的输出能被 _parse_grounding 正则匹配。

        验证 _replace_grounding_tags 也能正常处理。
        """
        import re

        cf = ColumnFilter()
        regions = _build_content(3)
        rebuilt = cf.rebuild_text(regions)

        # 验证 grounding 正则能匹配
        pattern = re.compile(
            r"<\|ref\|>(.*?)<\|/ref\|>"
            r"<\|det\|>(.*?)<\|/det\|>",
            re.DOTALL,
        )
        matches = pattern.findall(rebuilt)
        assert len(matches) == 3


class TestNeedsReocr:
    """needs_reocr 测试"""

    def test_normal_ratio(self) -> None:
        """正常占比 → 不需要重跑。"""
        cf = ColumnFilter()
        # 22 个总区域，10 个正文 → 45%
        assert cf.needs_reocr(22, 10) is False

    def test_too_few_content(self) -> None:
        """正文占比过低 → 需要重跑。"""
        cf = ColumnFilter()
        # 22 个总区域，2 个正文 → 9%
        assert cf.needs_reocr(22, 2) is True

    def test_too_many_content(self) -> None:
        """正文占比过高（几乎没过滤）→ 需要重跑。"""
        cf = ColumnFilter()
        # 10 个总区域，10 个正文 → 100%
        assert cf.needs_reocr(10, 10) is True

    def test_zero_total(self) -> None:
        """空输入 → 不需要重跑。"""
        cf = ColumnFilter()
        assert cf.needs_reocr(0, 0) is False

    def test_boundary_min_ratio(self) -> None:
        """恰好在 min_ratio 边界。"""
        cf = ColumnFilter()
        # 10 个总区域，2 个正文 → 20% = min_ratio
        assert cf.needs_reocr(10, 2) is False

    def test_boundary_max_ratio(self) -> None:
        """恰好在 max_ratio 边界。"""
        cf = ColumnFilter()
        # 100 个总区域，95 个正文 → 95% = max_ratio
        assert cf.needs_reocr(100, 95) is False


class TestComputeCropBox:
    """compute_crop_box 测试"""

    def test_basic_crop(self) -> None:
        """基本裁剪框计算。"""
        cf = ColumnFilter()
        boundaries = ColumnBoundaries(
            left_boundary=220,
            right_boundary=800,
            has_sidebar=True,
        )
        box = cf.compute_crop_box(boundaries, 1920, 1080)

        x1, y1, x2, y2 = box
        assert x1 == int(220 / 999 * 1920)
        assert y1 == 0
        assert x2 == int(800 / 999 * 1920)
        assert y2 == 1080

    def test_no_left_sidebar(self) -> None:
        """无左栏时 x1=0。"""
        cf = ColumnFilter()
        boundaries = ColumnBoundaries(
            left_boundary=0,
            right_boundary=800,
            has_sidebar=True,
        )
        box = cf.compute_crop_box(boundaries, 1920, 1080)
        assert box[0] == 0

    def test_no_right_sidebar(self) -> None:
        """无右栏时 x2=图片宽度。"""
        cf = ColumnFilter()
        boundaries = ColumnBoundaries(
            left_boundary=220,
            right_boundary=999,
            has_sidebar=True,
        )
        box = cf.compute_crop_box(boundaries, 1920, 1080)
        assert box[2] == 1920


class TestParseGroundingRegions:
    """parse_grounding_regions 测试"""

    def test_parse_basic(self) -> None:
        """解析基本的 grounding 文本。"""
        raw = (
            "<|ref|>text<|/ref|>"
            "<|det|>[[50, 100, 700, 150]]<|/det|>\n"
            "这是一行正文内容"
        )
        cf = ColumnFilter()
        regions = cf.parse_grounding_regions(raw)

        assert len(regions) == 1
        assert regions[0].label == "text"
        assert regions[0].x1 == 50
        assert regions[0].y1 == 100
        assert regions[0].x2 == 700
        assert regions[0].y2 == 150
        assert "正文内容" in regions[0].text

    def test_parse_multiple_regions(self) -> None:
        """解析多个 grounding 区域。"""
        raw = (
            "<|ref|>text<|/ref|>"
            "<|det|>[[10, 50, 200, 80]]<|/det|>\n"
            "导航项\n"
            "<|ref|>text<|/ref|>"
            "<|det|>[[250, 50, 750, 80]]<|/det|>\n"
            "正文"
        )
        cf = ColumnFilter()
        regions = cf.parse_grounding_regions(raw)

        assert len(regions) == 2

    def test_parse_empty_text(self) -> None:
        """空文本。"""
        cf = ColumnFilter()
        regions = cf.parse_grounding_regions("")

        assert regions == []

    def test_parse_invalid_coords(self) -> None:
        """坐标解析失败时跳过。"""
        raw = (
            "<|ref|>text<|/ref|>"
            "<|det|>invalid_coords<|/det|>\n"
            "内容"
        )
        cf = ColumnFilter()
        regions = cf.parse_grounding_regions(raw)

        assert regions == []


def _build_left_narrow_labels(
    count: int = 6,
) -> list[GroundingRegion]:
    """生成左侧窄区域（图表标签/列表项），满足侧栏候选条件。

    y 范围 120-820，确保纵向展幅足够通过垂直展幅校验。
    """
    step = max(1, 700 // max(count, 1))
    return [
        _make_region(
            20, 120 + i * step, 180, 150 + i * step,
            text=f"label_{i}",
        )
        for i in range(count)
    ]


def _build_left_starting_content(
    count: int = 5,
) -> list[GroundingRegion]:
    """生成从左侧开始的宽正文区域（x1 < 200, x2 > 500）。"""
    return [
        _make_region(
            30, 300 + i * 60, 700, 340 + i * 60,
            text=f"wide_content_{i}",
        )
        for i in range(count)
    ]


class TestColumnLayoutValidation:
    """分栏验证：防止误判非分栏页面的左侧短文本为侧栏"""

    def test_chart_labels_not_sidebar(self) -> None:
        """图表标签场景：多个窄左侧区域 + 多个从左开始的宽正文 → 非侧栏。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = (
            _build_left_narrow_labels(6)
            + _build_left_starting_content(5)
            + _build_content(5)
        )
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is False
        assert boundaries.left_boundary == 0

    def test_true_sidebar_still_detected(self) -> None:
        """真侧栏场景：正文不从左侧开始 → 仍然检测到侧栏。"""
        cf = ColumnFilter(min_sidebar_count=5)
        # 左栏 + 正文（x1=250，不在 left_boundary 内）
        regions = _build_left_sidebar(6) + _build_content(10)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary > 0

    def test_borderline_two_left_main(self) -> None:
        """边界：只有 2 个从左开始的正文 → 仍判定为侧栏（阈值 3）。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = (
            _build_left_narrow_labels(6)
            + _build_left_starting_content(2)
            + _build_content(10)
        )
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary > 0


def _build_full_width_headers(
    count: int = 4,
) -> list[GroundingRegion]:
    """生成跨全宽的头部/横幅区域（模拟浏览器标题栏、面包屑等）。"""
    return [
        _make_region(
            0, i * 20, 999, 15 + i * 20,
            text=f"header_{i}",
        )
        for i in range(count)
    ]


class TestFullWidthHeaderExclusion:
    """跨全宽元素不应干扰侧栏验证"""

    def test_headers_do_not_invalidate_left_sidebar(self) -> None:
        """三栏页面 + 跨全宽头部 → 左栏仍被检测到。

        修复场景：浏览器顶栏、面包屑等跨全宽元素的 x1 < left_boundary，
        不应计入"从左边开始的正文"导致左栏验证失败。
        """
        cf = ColumnFilter(min_sidebar_count=5)
        regions = (
            _build_full_width_headers(4)
            + _build_left_sidebar(6)
            + _build_content(10)
            + _build_right_sidebar(6)
        )
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary > 0
        assert boundaries.right_boundary < 999

    def test_headers_do_not_invalidate_right_sidebar(self) -> None:
        """右栏 + 跨全宽头部 → 右栏仍被检测到。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = (
            _build_full_width_headers(4)
            + _build_content(10)
            + _build_right_sidebar(6)
        )
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.right_boundary < 999

    def test_many_headers_still_detect_sidebar(self) -> None:
        """大量跨全宽头部（超过 30% 非候选区域） → 仍能检测侧栏。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = (
            _build_full_width_headers(8)  # 8 个全宽头部
            + _build_left_sidebar(6)
            + _build_content(6)  # 6 个正文
        )
        # 非候选区域 = 8 + 6 = 14，30% = 4.2
        # 修复前：8 个全宽头部的 x1=0 < left_boundary → 验证失败
        # 修复后：全宽头部被排除，只看 6 个正文
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary > 0


class TestEndToEnd:
    """端到端场景测试"""

    def test_full_pipeline_three_column(self) -> None:
        """三栏页面完整流程：检测 → 过滤 → 重建。"""
        cf = ColumnFilter(min_sidebar_count=5)

        # 构造三栏区域
        left = _build_left_sidebar(6)
        content = _build_content(8)
        right = _build_right_sidebar(6)
        all_regions = left + content + right

        # 检测
        boundaries = cf.detect_boundaries(all_regions)
        assert boundaries.has_sidebar is True

        # 过滤
        filtered = cf.filter_regions(all_regions, boundaries)
        assert len(filtered) == len(content)

        # 不需要重跑
        assert cf.needs_reocr(
            len(all_regions), len(filtered)
        ) is False

        # 重建
        rebuilt = cf.rebuild_text(filtered)
        assert "content_line_0" in rebuilt
        assert "nav_item_0" not in rebuilt
        assert "toc_item_0" not in rebuilt

    @pytest.mark.parametrize(
        ("left_count", "right_count", "expect_sidebar"),
        [
            (6, 6, True),
            (6, 0, True),
            (0, 6, True),
            (3, 3, False),
            (0, 0, False),
        ],
    )
    def test_sidebar_detection_variants(
        self,
        left_count: int,
        right_count: int,
        expect_sidebar: bool,
    ) -> None:
        """参数化测试各种侧栏组合。"""
        cf = ColumnFilter(min_sidebar_count=5)
        regions = (
            _build_left_sidebar(left_count)
            + _build_content(10)
            + _build_right_sidebar(right_count)
        )
        boundaries = cf.detect_boundaries(regions)
        assert boundaries.has_sidebar is expect_sidebar


class TestBrowserChromeFiltering:
    """浏览器 Chrome 区域过滤：屏幕照片顶部标签/地址栏不应干扰侧栏检测"""

    @staticmethod
    def _build_browser_tabs(count: int = 6) -> list[GroundingRegion]:
        """模拟浏览器标签栏文本（y < 80，x 在左侧窄区域）。

        这些区域与左栏候选条件重叠（x1<100, x2<=220），
        但属于浏览器 Chrome 而非文档侧栏。
        """
        return [
            _make_region(
                10 + i * 30, 10, 80 + i * 30, 40,
                text=f"tab_{i}",
            )
            for i in range(count)
        ]

    def test_browser_tabs_not_false_left_sidebar(self) -> None:
        """浏览器标签不应被误判为左栏。

        场景：无真实左栏，但顶部标签窄文本满足
        x1<100, x2<=220 且数量>=5，不应触发裁剪。
        """
        cf = ColumnFilter(min_sidebar_count=5)
        regions = self._build_browser_tabs(8) + _build_content(10)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is False
        assert boundaries.left_boundary == 0

    def test_real_sidebar_not_blocked_by_chrome(self) -> None:
        """浏览器 Chrome 不应影响真实左栏的验证。

        场景：真实左栏存在，但浏览器地址栏等宽文本从左侧开始，
        不应在验证阶段被误计为"正文从左开始"而否决左栏检测。
        """
        cf = ColumnFilter(min_sidebar_count=5)
        # 浏览器 Chrome：URL 栏等宽文本，y < 80，从左侧开始
        chrome = [
            _make_region(20, 10, 600, 40, text="url_bar"),
            _make_region(10, 45, 400, 70, text="bookmark_bar"),
            _make_region(50, 5, 200, 25, text="tab_1"),
            _make_region(210, 5, 400, 25, text="tab_2"),
        ]
        regions = chrome + _build_left_sidebar(6) + _build_content(10)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is True
        assert boundaries.left_boundary > 0

    def test_sidebar_candidates_need_vertical_spread(self) -> None:
        """聚集在窄 y 范围内的候选不算侧栏（即使数量足够）。

        模拟：页面某一段有多个窄标签聚集，但不贯穿页面。
        """
        cf = ColumnFilter(min_sidebar_count=5)
        # 6 个候选聚集在 y=200-350，展幅仅 150 < 300
        clustered = [
            _make_region(
                10, 200 + i * 25, 180, 220 + i * 25,
                text=f"label_{i}",
            )
            for i in range(6)
        ]
        regions = clustered + _build_content(10)
        boundaries = cf.detect_boundaries(regions)

        assert boundaries.has_sidebar is False
