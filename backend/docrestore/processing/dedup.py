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

"""相邻页重叠检测与合并

使用 difflib.SequenceMatcher 做模糊匹配，滚动合并所有页面。
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from difflib import SequenceMatcher

from docrestore.models import (
    MergedDocument,
    MergeResult,
    PageOCR,
    Region,
)
from docrestore.pipeline.config import DedupConfig

logger = logging.getLogger(__name__)


def _normalize_line(line: str) -> str:
    """归一化行文本：去首尾空白 + 压缩连续空格。"""
    return " ".join(line.split())


def _mark_noise_blocks(
    lines: list[str],
    noise_lines: set[str],
    min_block: int,
) -> list[bool]:
    """标记需要移除的连续噪声行块。

    扫描 lines，将连续 ≥ min_block 行的噪声块标记为 True。

    Args:
        lines: 页面文本行列表
        noise_lines: 已识别的噪声行集合（归一化后）
        min_block: 连续噪声行最小块大小

    Returns:
        与 lines 等长的布尔列表，True 表示该行应被移除
    """
    is_noise = [_normalize_line(line) in noise_lines for line in lines]
    remove = [False] * len(lines)
    block_start = -1

    for i, noisy in enumerate(is_noise):
        if noisy:
            if block_start < 0:
                block_start = i
        else:
            if block_start >= 0 and (i - block_start) >= min_block:
                for j in range(block_start, i):
                    remove[j] = True
            block_start = -1

    # 处理末尾的连续块
    if block_start >= 0 and (len(lines) - block_start) >= min_block:
        for j in range(block_start, len(lines)):
            remove[j] = True

    return remove


def strip_repeated_lines(
    pages: list[PageOCR],
    config: DedupConfig,
) -> None:
    """跨页频率过滤：移除在多数页面中重复出现的侧栏噪声行。

    **原地修改** 每页的 cleaned_text。

    算法：
    1. 逐页按行拆分，对每行归一化后统计出现在多少个不同页面中
    2. 出现频率 ≥ threshold 的行标记为噪声
    3. 在每页中，只移除连续 ≥ min_block 行的噪声块（防误删孤立重复行）

    Args:
        pages: OCR 清洗后的页面列表（会原地修改 cleaned_text）
        config: 去重配置（含频率过滤参数）
    """
    total = len(pages)
    if total < config.repeated_line_min_pages:
        return

    threshold_count = max(1, int(total * config.repeated_line_threshold))

    # 统计每个归一化行出现在多少个不同页面中
    line_page_count: Counter[str] = Counter()
    for page in pages:
        text = page.cleaned_text or page.raw_text
        # 每页只计一次（set 去重同一页内的重复行）
        unique_lines = {
            _normalize_line(line)
            for line in text.splitlines()
            if len(line.strip()) > 1  # 跳过空行和单字符行
        }
        line_page_count.update(unique_lines)

    # 筛选噪声行集合
    noise_lines: set[str] = {
        line
        for line, count in line_page_count.items()
        if count >= threshold_count
    }

    if not noise_lines:
        return

    logger.info(
        "跨页频率过滤: 检测到 %d 个噪声行 (阈值=%d/%d 页)",
        len(noise_lines), threshold_count, total,
    )

    # 逐页移除连续噪声块
    min_block = config.repeated_line_min_block
    removed_total = 0
    for page in pages:
        text = page.cleaned_text or page.raw_text
        lines = text.splitlines()
        remove = _mark_noise_blocks(lines, noise_lines, min_block)
        kept = [line for line, rm in zip(lines, remove, strict=True) if not rm]
        removed_count = len(lines) - len(kept)
        if removed_count > 0:
            removed_total += removed_count
            page.cleaned_text = "\n".join(kept)

    if removed_total > 0:
        logger.info("跨页频率过滤: 共移除 %d 行噪声", removed_total)


class PageDeduplicator:
    """相邻页重叠检测与合并"""

    def __init__(self, config: DedupConfig) -> None:
        self._config = config

    def _extract_markers_and_content(
        self, lines: list[str]
    ) -> tuple[list[tuple[int, str]], list[str]]:
        """从文本行中提取 page markers 和纯内容。

        Returns:
            (markers, content_lines)
            markers: [(插入位置, marker文本), ...]
            content_lines: 不含 marker 的内容行
        """
        markers: list[tuple[int, str]] = []
        content_lines: list[str] = []
        for line in lines:
            if line.startswith("<!-- page:"):
                markers.append((len(content_lines), line))
            else:
                content_lines.append(line)
        return markers, content_lines

    def merge_two_pages(
        self, text_a: str, text_b: str
    ) -> MergeResult:
        """合并两页文本，检测并去除重叠区域。

        Page marker 行（<!-- page: xxx -->）会被保留，不参与重叠检测。
        """
        lines_a = text_a.splitlines()
        lines_b = text_b.splitlines()

        if not lines_a or not lines_b:
            combined = text_a + "\n" + text_b
            return MergeResult(
                text=combined, overlap_lines=0, similarity=0.0
            )

        # 提取 markers 和纯内容
        a_markers, a_content_lines = self._extract_markers_and_content(lines_a)
        b_markers, b_content_lines = self._extract_markers_and_content(lines_b)

        # 对纯内容做重叠检测
        ratio = self._config.search_ratio
        tail_count = max(1, int(len(a_content_lines) * ratio))
        head_count = max(1, int(len(b_content_lines) * ratio))
        tail_a = a_content_lines[-tail_count:]
        head_b = b_content_lines[:head_count]

        # 用 SequenceMatcher 找最长匹配块
        matcher = SequenceMatcher(None, tail_a, head_b)
        match = matcher.find_longest_match(
            0, len(tail_a), 0, len(head_b)
        )

        if match.size == 0:
            combined = text_a + "\n\n" + text_b
            return MergeResult(
                text=combined, overlap_lines=0, similarity=0.0
            )

        # 检查匹配区域中非空行数量，太少则视为无效重叠
        matched_lines = tail_a[match.a : match.a + match.size]
        non_empty_count = sum(
            1 for line in matched_lines if line.strip()
        )
        if non_empty_count < 2:
            combined = text_a + "\n\n" + text_b
            return MergeResult(
                text=combined, overlap_lines=0, similarity=0.0
            )

        # 计算匹配区域的相似度
        matched_a = "\n".join(matched_lines)
        matched_b = "\n".join(
            head_b[match.b : match.b + match.size]
        )
        similarity = SequenceMatcher(
            None, matched_a, matched_b
        ).ratio()

        if similarity < self._config.similarity_threshold:
            combined = text_a + "\n\n" + text_b
            return MergeResult(
                text=combined,
                overlap_lines=0,
                similarity=similarity,
            )

        # 计算重叠位置（在纯内容中）
        overlap_start_in_a = len(a_content_lines) - tail_count + match.a
        overlap_end_in_a = overlap_start_in_a + match.size
        overlap_end_in_b = match.b + match.size

        # 合并纯内容：A 的全部 + B 的非重叠部分
        merged_content = (
            a_content_lines[:overlap_end_in_a]
            + b_content_lines[overlap_end_in_b:]
        )

        # 重新插入所有 page markers
        result_lines: list[str] = []
        content_idx = 0

        # 插入 A 的 markers
        for pos, marker in a_markers:
            while content_idx < pos and content_idx < len(merged_content):
                result_lines.append(merged_content[content_idx])
                content_idx += 1
            result_lines.append(marker)

        # 插入 B 的 markers（位置需要调整：减去重叠部分）
        for pos, marker in b_markers:
            adjusted_pos = len(a_content_lines) + pos - overlap_end_in_b
            while content_idx < adjusted_pos and content_idx < len(merged_content):
                result_lines.append(merged_content[content_idx])
                content_idx += 1
            result_lines.append(marker)

        # 插入剩余内容
        while content_idx < len(merged_content):
            result_lines.append(merged_content[content_idx])
            content_idx += 1

        combined = "\n".join(result_lines)
        return MergeResult(
            text=combined,
            overlap_lines=match.size,
            similarity=similarity,
        )

    def merge_all_pages(
        self,
        pages: list[PageOCR],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> MergedDocument:
        """滚动合并所有页面。

        - 每页头部插入 <!-- page: {filename} --> 标记
        - 图片引用从 ![](images/N.jpg) 重写为 ![]({stem}_OCR/images/N.jpg)
        - 收集所有页的 regions 汇总到 images

        合并策略：每页文本先 prepend page marker + 重写图片引用，
        再滚动调用 merge_two_pages 做重叠检测合并。
        page marker 行是唯一的（含不同文件名），不会被误判为重叠。
        """
        if not pages:
            return MergedDocument(markdown="")

        all_images: list[Region] = []
        total = len(pages) - 1 if len(pages) > 1 else 1

        # 准备各页文本：prepend page marker + 重写图片引用
        page_texts: list[str] = []
        for page in pages:
            marker = f"<!-- page: {page.image_path.name} -->"
            body = self._rewrite_image_refs(page)
            page_texts.append(f"{marker}\n{body}")
            all_images.extend(page.regions)

        # 滚动合并（带 page marker 的文本）
        merged_text = page_texts[0]
        for i in range(1, len(page_texts)):
            result = self.merge_two_pages(
                merged_text, page_texts[i]
            )
            merged_text = result.text
            if on_progress is not None:
                on_progress(i, total)

        return MergedDocument(
            markdown=merged_text.rstrip("\n"),
            images=all_images,
        )

    def _rewrite_image_refs(self, page: PageOCR) -> str:
        """重写图片引用（支持 markdown 和 HTML img 两种格式）。

        使用 page.output_dir.name 作为 OCR 目录名，
        确保裁剪页面（{stem}_cropped_OCR）也能正确指向。
        """
        # output_dir 已包含正确的 OCR 目录名（含 _cropped 后缀），
        # 回退到 image_path.stem + _OCR 以兼容 output_dir 为 None 的情况
        if page.output_dir is not None:
            ocr_dirname = page.output_dir.name
        else:
            ocr_dirname = f"{page.image_path.stem}_OCR"
        text = page.cleaned_text or page.raw_text
        # markdown 格式：![alt](images/...) → ![alt]({ocr_dirname}/images/...)
        text = re.sub(
            r"!\[([^\]]*)\]\(images/",
            rf"![\1]({ocr_dirname}/images/",
            text,
        )
        # HTML 格式：src="images/..." → src="{ocr_dirname}/images/..."
        return re.sub(
            r'src="images/',
            f'src="{ocr_dirname}/images/',
            text,
        )
