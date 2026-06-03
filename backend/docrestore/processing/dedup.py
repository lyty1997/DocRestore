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


def rewrite_image_refs_to_ocr_dir(page: PageOCR) -> str:
    """把单页 markdown 里的相对图片引用 ``images/N`` 加上该页 OCR 目录前缀
    （``{ocr_dir}/images/N``），供多页合并后 ``Renderer`` 统一复制与重写。

    支持 markdown ``![](images/..)`` 与 HTML ``src="images/.."`` 两种格式；
    用 ``page.output_dir.name`` 作前缀（兼容 ``{stem}_cropped_OCR``），
    ``output_dir`` 为 None 时回退 ``{stem}_OCR``。文档模式与 PPT 模式共用，
    确保图片引用重写规则单一真相源。
    """
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


def _normalize_line(line: str) -> str:
    """归一化行文本：去首尾空白 + 压缩连续空格。"""
    return " ".join(line.split())


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

    def _find_suffix_prefix_overlap(
        self,
        a_content: list[str],
        b_content: list[str],
    ) -> int:
        """返回满足 A[-k:] 归一化等于 B[:k] 归一化的最大 k。

        锚定到 A 真正尾部和 B 真正头部，避免 find_longest_match 误中页中间巧合。
        - 归一化：压缩连续空白，空白行与 "" 视为相等
        - 非空行数 < 2 时返回 0（单行重叠不够可信）
        - 搜索上限：页面重叠一般 ≤ 20 行
        """
        max_k = min(len(a_content), len(b_content), 20)
        if max_k < 1:
            return 0

        norm_a = [_normalize_line(line) for line in a_content]
        norm_b = [_normalize_line(line) for line in b_content]

        for k in range(max_k, 0, -1):
            if norm_a[-k:] != norm_b[:k]:
                continue
            non_empty = sum(1 for line in norm_a[-k:] if line)
            if non_empty >= 2:
                return k
        return 0

    def merge_two_pages(  # noqa: C901 — 合并分支必要
        self, text_a: str, text_b: str
    ) -> MergeResult:
        """合并两页文本，检测并去除重叠区域。

        检测策略：
        1. 先做 suffix-prefix 精确锚定（A 真正尾部 == B 真正头部）
        2. 否则退回 SequenceMatcher 最长块，但要求**同时**锚定 A 尾 + B 头

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

        # 路径 1：suffix-prefix 精确锚定
        sp_overlap = self._find_suffix_prefix_overlap(
            a_content_lines, b_content_lines,
        )
        if sp_overlap > 0:
            overlap_end_in_a = len(a_content_lines)
            overlap_end_in_b = sp_overlap
            match_size_for_report = sp_overlap
            similarity = 1.0
            return self._assemble_merge(
                text_a, text_b,
                a_markers, a_content_lines,
                b_markers, b_content_lines,
                overlap_end_in_a, overlap_end_in_b,
                match_size_for_report, similarity,
            )

        # 路径 2：对纯内容做 SequenceMatcher 最长块检测
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

        # **关键约束**：匹配块必须同时锚定 A 真尾部和 B 真头部。
        # - match.a + match.size == len(tail_a)：块贴在 tail_a 末尾
        # - match.b == 0：块贴在 head_b 开头
        # 任一不满足说明匹配到了页中间的巧合重复，不是真的页面重叠
        anchored_at_a_tail = (match.a + match.size) == len(tail_a)
        anchored_at_b_head = match.b == 0
        if not (anchored_at_a_tail and anchored_at_b_head):
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

        return self._assemble_merge(
            text_a, text_b,
            a_markers, a_content_lines,
            b_markers, b_content_lines,
            overlap_end_in_a, overlap_end_in_b,
            match.size, similarity,
        )

    def _assemble_merge(  # noqa: PLR0913 — 参数都是重组合并位置索引
        self,
        text_a: str,
        text_b: str,
        a_markers: list[tuple[int, str]],
        a_content_lines: list[str],
        b_markers: list[tuple[int, str]],
        b_content_lines: list[str],
        overlap_end_in_a: int,
        overlap_end_in_b: int,
        match_size: int,
        similarity: float,
    ) -> MergeResult:
        """根据给定的重叠索引，拼接合并后的文本 + 重插 page markers。"""
        _ = text_a, text_b  # 调用方保留原文本仅供签名兼容

        # 合并纯内容：A 的全部（含重叠部分的 A 版本） + B 的非重叠部分
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
            overlap_lines=match_size,
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
        """重写图片引用：委托模块级 ``rewrite_image_refs_to_ocr_dir``。"""
        return rewrite_image_refs_to_ocr_dir(page)


class IncrementalMerger:
    """流式增量合并器：逐页 `add_page()` 后可 `get_markdown()` 查看累积文本。

    设计约束（见 streaming-pipeline.md §4.1）：
    - 对相同输入，`IncrementalMerger` 逐页 `add_page(p)` 后 `get_markdown()`
      必须与 `PageDeduplicator.merge_all_pages(pages).markdown` 完全一致。
    - 底层直接复用 `PageDeduplicator.merge_two_pages()` 和 `_rewrite_image_refs`。
    - 不维护页面归属查询（get_page_names_up_to 等）—— 单文档简化后不需要。
    """

    def __init__(self, config: DedupConfig) -> None:
        self._dedup = PageDeduplicator(config)
        self._merged_markdown: str = ""
        self._page_names: list[str] = []
        self._all_regions: list[Region] = []

    def add_page(self, page: PageOCR) -> None:
        """合并新页到累积文本，更新 page_names / regions 记录。"""
        marker = f"<!-- page: {page.image_path.name} -->"
        body = self._dedup._rewrite_image_refs(page)  # noqa: SLF001
        new_page_text = f"{marker}\n{body}"

        if not self._merged_markdown:
            self._merged_markdown = new_page_text
        else:
            result = self._dedup.merge_two_pages(
                self._merged_markdown, new_page_text,
            )
            self._merged_markdown = result.text

        self._page_names.append(page.image_path.name)
        self._all_regions.extend(page.regions)

    def get_markdown(self) -> str:
        """返回当前合并的 markdown（与 merge_all_pages 一致，末尾去换行）。"""
        return self._merged_markdown.rstrip("\n")

    def get_all_images(self) -> list[Region]:
        """返回所有已合并页面的 Region 汇总。"""
        return list(self._all_regions)

    @property
    def page_count(self) -> int:
        """已合并的页面数。"""
        return len(self._page_names)
