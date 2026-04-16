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

"""侧栏检测与过滤

从 OCR grounding 坐标中检测左栏（导航目录）和右栏（大纲/TOC），
过滤侧栏区域后重建纯正文的 grounding 文本。

坐标系：DeepSeek-OCR-2 grounding 使用归一化坐标 0-999。
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass

from docrestore.pipeline.config import ColumnFilterThresholds

logger = logging.getLogger(__name__)


@dataclass
class ColumnBoundaries:
    """列边界检测结果"""

    left_boundary: int = 0  # 左栏右边界（归一化 0-coord_range）
    right_boundary: int = 999  # 右栏左边界（归一化 0-coord_range）
    has_sidebar: bool = False  # 是否检测到侧栏


@dataclass
class GroundingRegion:
    """带坐标的 grounding 区域"""

    label: str
    x1: int
    y1: int
    x2: int
    y2: int
    text: str  # grounding 后跟的文本内容
    raw_block: str  # 原始 grounding 块（含标签，用于重建）


# grounding 块正则：匹配 <|ref|>...<|/ref|><|det|>...<|/det|> 及其后续文本
_GROUNDING_BLOCK_RE = re.compile(
    r"(<\|ref\|>(.*?)<\|/ref\|>"
    r"<\|det\|>(.*?)<\|/det\|>)"
    r"\n(.*?)(?=\n<\|ref\||$)",
    re.DOTALL,
)


class ColumnFilter:
    """侧栏检测与过滤

    输入图片是屏幕拍摄照片，顶部通常包含浏览器标签栏/地址栏，
    底部可能含有显示器边框/任务栏。检测前需排除这些区域以免干扰。
    """

    def __init__(
        self,
        min_sidebar_count: int = 5,
        thresholds: ColumnFilterThresholds | None = None,
    ) -> None:
        """初始化。

        Args:
            min_sidebar_count: 最少侧栏区域数才触发过滤
            thresholds: 坐标阈值集合（None 使用默认值）
        """
        self._min_sidebar_count = min_sidebar_count
        self._t = thresholds or ColumnFilterThresholds()

    def parse_grounding_regions(
        self, raw_text: str
    ) -> list[GroundingRegion]:
        """从 result_ori.mmd 解析所有 grounding 区域及其文本。

        每个区域保留原始 grounding 块（含 <|ref|>...<|det|>... 标签），
        以及紧跟其后的文本内容。
        """
        regions: list[GroundingRegion] = []

        for m in _GROUNDING_BLOCK_RE.finditer(raw_text):
            grounding_tag = m.group(1)  # 完整的 ref+det 标签
            label = m.group(2)
            coords_str = m.group(3)
            text_after = m.group(4).strip()

            try:
                coords_list = ast.literal_eval(coords_str)
            except (ValueError, SyntaxError):
                logger.warning(
                    "grounding 坐标解析失败: %s",
                    coords_str[:50],
                )
                continue

            # 完整的原始块 = grounding 标签 + 换行 + 后续文本
            raw_block = (
                f"{grounding_tag}\n{text_after}"
                if text_after
                else grounding_tag
            )

            for coords in coords_list:
                if len(coords) != 4:
                    continue
                x1, y1, x2, y2 = (
                    int(coords[0]),
                    int(coords[1]),
                    int(coords[2]),
                    int(coords[3]),
                )
                regions.append(
                    GroundingRegion(
                        label=label,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        text=text_after,
                        raw_block=raw_block,
                    )
                )

        return regions

    def _filter_content_regions(
        self, regions: list[GroundingRegion],
    ) -> list[GroundingRegion]:
        """排除浏览器 Chrome 区域，只保留文档内容区域。

        屏幕照片的顶部通常包含浏览器标签栏/地址栏/书签栏，
        这些窄文本会干扰侧栏检测（误判为左栏候选或影响验证计数）。
        """
        return [
            r for r in regions
            if r.y1 >= self._t.chrome_y_threshold
        ]

    @staticmethod
    def _has_vertical_spread(
        candidates: list[GroundingRegion],
        min_spread: int,
    ) -> bool:
        """检查候选区域是否纵向跨越足够高度。

        真实侧栏（导航/目录）纵向贯穿页面大部分区域；
        浏览器标签等聚集在顶部的文本垂直展幅很小，以此排除。
        """
        if not candidates:
            return False
        y_min = min(r.y1 for r in candidates)
        y_max = max(r.y2 for r in candidates)
        return (y_max - y_min) >= min_spread

    def detect_boundaries(
        self, regions: list[GroundingRegion]
    ) -> ColumnBoundaries:
        """自适应检测列边界。

        左栏候选：x1 < left_candidate_max_x1 且 x2 <= left_candidate_max_x2
        右栏候选：x1 >= right_candidate_min_x1 且 width < right_candidate_max_width

        防误判措施：
        - 排除浏览器 Chrome 区域（y 轴顶部区）的文本
        - 要求候选区域纵向展幅 >= min_sidebar_y_spread
        - 分栏验证排除全宽元素（宽度 >= full_width_threshold）
        """
        t = self._t
        if not regions:
            return ColumnBoundaries(right_boundary=t.coord_range)

        # 排除浏览器 Chrome 区域后再检测侧栏
        content = self._filter_content_regions(regions)
        if not content:
            return ColumnBoundaries(right_boundary=t.coord_range)

        # 检测左栏
        left_candidates = [
            r
            for r in content
            if r.x1 < t.left_candidate_max_x1
            and r.x2 <= t.left_candidate_max_x2
        ]
        left_boundary = 0
        has_left = (
            len(left_candidates) >= self._min_sidebar_count
            and self._has_vertical_spread(
                left_candidates, t.min_sidebar_y_spread,
            )
        )
        if has_left:
            left_boundary = (
                max(r.x2 for r in left_candidates)
                + t.left_boundary_padding
            )
            # 分栏验证：非候选区域中从左侧开始的数量
            # 如果正文也从左边开始，说明不是真的分栏布局
            # 排除跨全宽元素（头部/尾部横幅不代表正文起始位置）
            left_ids = {id(r) for r in left_candidates}
            non_cands = [
                r
                for r in content
                if id(r) not in left_ids
                and (r.x2 - r.x1) < t.full_width_threshold
            ]
            left_starting_main = sum(
                1
                for r in non_cands
                if r.x1 < left_boundary
            )
            threshold = max(
                t.min_validation_count,
                len(non_cands) * t.main_content_ratio_threshold,
            )
            if left_starting_main >= threshold:
                logger.debug(
                    "左栏验证失败: %d/%d "
                    "非候选区域从左边开始 (阈值=%.1f)",
                    left_starting_main, len(non_cands), threshold,
                )
                has_left = False
                left_boundary = 0

        # 检测右栏
        right_candidates = [
            r
            for r in content
            if r.x1 >= t.right_candidate_min_x1
            and (r.x2 - r.x1) < t.right_candidate_max_width
        ]
        right_boundary = t.coord_range
        has_right = (
            len(right_candidates) >= self._min_sidebar_count
            and self._has_vertical_spread(
                right_candidates, t.min_sidebar_y_spread,
            )
        )
        if has_right:
            right_boundary = (
                min(r.x1 for r in right_candidates)
                - t.right_boundary_padding
            )
            # 分栏验证：非候选区域中延伸到右侧的数量
            # 排除跨全宽元素（头部/尾部横幅不代表正文结束位置）
            right_ids = {id(r) for r in right_candidates}
            non_cands_r = [
                r
                for r in content
                if id(r) not in right_ids
                and (r.x2 - r.x1) < t.full_width_threshold
            ]
            right_ending_main = sum(
                1
                for r in non_cands_r
                if r.x2 > right_boundary
            )
            threshold_r = max(
                t.min_validation_count,
                len(non_cands_r) * t.main_content_ratio_threshold,
            )
            if right_ending_main >= threshold_r:
                logger.debug(
                    "右栏验证失败: %d/%d "
                    "非候选区域延伸到右侧 (阈值=%.1f)",
                    right_ending_main, len(non_cands_r), threshold_r,
                )
                has_right = False
                right_boundary = t.coord_range

        return ColumnBoundaries(
            left_boundary=left_boundary,
            right_boundary=right_boundary,
            has_sidebar=has_left or has_right,
        )

    def filter_regions(
        self,
        regions: list[GroundingRegion],
        boundaries: ColumnBoundaries,
    ) -> list[GroundingRegion]:
        """过滤侧栏区域，只保留正文。

        左栏判定：x1 < left_candidate_max_x1
                 且 x2 <= left_boundary + left_filter_padding
        右栏判定：x1 >= right_boundary
        """
        t = self._t
        content: list[GroundingRegion] = []
        for r in regions:
            # 左栏过滤
            if (
                boundaries.left_boundary > 0
                and r.x1 < t.left_candidate_max_x1
                and r.x2 <= boundaries.left_boundary + t.left_filter_padding
            ):
                continue
            # 右栏过滤
            if (
                boundaries.right_boundary < t.coord_range
                and r.x1 >= boundaries.right_boundary
            ):
                continue
            content.append(r)
        return content

    def rebuild_text(
        self, content_regions: list[GroundingRegion]
    ) -> str:
        """从正文区域重建 result_ori.mmd 格式的文本。

        保留 grounding 标签格式，后续 _parse_grounding / _replace_grounding_tags
        无需修改。
        """
        return "\n".join(
            r.raw_block for r in content_regions
        )

    def needs_reocr(
        self,
        total_count: int,
        content_count: int,
        min_ratio: float | None = None,
        max_ratio: float | None = None,
    ) -> bool:
        """正文占比异常时需要裁剪重跑 OCR。

        Args:
            total_count: 总区域数
            content_count: 正文区域数
            min_ratio: 正文占比下限（None 使用 thresholds.content_min_ratio）
            max_ratio: 正文占比上限（None 使用 thresholds.content_max_ratio）
        """
        if total_count == 0:
            return False
        lo = self._t.content_min_ratio if min_ratio is None else min_ratio
        hi = self._t.content_max_ratio if max_ratio is None else max_ratio
        ratio = content_count / total_count
        return ratio < lo or ratio > hi

    def compute_crop_box(
        self,
        boundaries: ColumnBoundaries,
        image_width: int,
        image_height: int,
    ) -> tuple[int, int, int, int]:
        """归一化坐标 → 像素坐标裁剪框。

        Returns:
            (x1, y1, x2, y2) 像素坐标
        """
        coord_range = self._t.coord_range
        x1 = int(boundaries.left_boundary / coord_range * image_width)
        x2 = int(
            boundaries.right_boundary / coord_range * image_width
        )
        return (x1, 0, x2, image_height)
