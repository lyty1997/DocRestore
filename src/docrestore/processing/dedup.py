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


class PageDeduplicator:
    """相邻页重叠检测与合并"""

    def __init__(self, config: DedupConfig) -> None:
        self._config = config

    def merge_two_pages(
        self, text_a: str, text_b: str
    ) -> MergeResult:
        """合并两页文本，检测并去除重叠区域。"""
        lines_a = text_a.splitlines()
        lines_b = text_b.splitlines()

        if not lines_a or not lines_b:
            combined = text_a + "\n" + text_b
            return MergeResult(
                text=combined, overlap_lines=0, similarity=0.0
            )

        # 取 A 尾部和 B 头部做匹配
        ratio = self._config.search_ratio
        tail_count = max(1, int(len(lines_a) * ratio))
        head_count = max(1, int(len(lines_b) * ratio))
        tail_a = lines_a[-tail_count:]
        head_b = lines_b[:head_count]

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

        # 计算实际行号位置
        overlap_start_in_a = len(lines_a) - tail_count + match.a
        overlap_end_in_a = overlap_start_in_a + match.size
        overlap_end_in_b = match.b + match.size

        # 拼接：A 的非重叠部分 + 重叠区域（保留一份） + B 的非重叠部分
        before = "\n".join(lines_a[:overlap_start_in_a])
        overlap = "\n".join(
            lines_a[overlap_start_in_a:overlap_end_in_a]
        )
        after = "\n".join(lines_b[overlap_end_in_b:])

        parts: list[str] = []
        if before:
            parts.append(before)
        parts.append(overlap)
        if after:
            parts.append(after)

        combined = "\n".join(parts)
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

        合并策略：先用不带页标记的纯文本做重叠检测和合并，
        合并完成后再插入页标记，避免页标记干扰重叠匹配。
        """
        if not pages:
            return MergedDocument(markdown="")

        all_images: list[Region] = []
        total = len(pages) - 1 if len(pages) > 1 else 1

        # 准备各页纯文本（不含页标记）和元信息
        page_texts: list[str] = []
        page_filenames: list[str] = []
        for page in pages:
            page_texts.append(self._rewrite_image_refs(page))
            page_filenames.append(page.image_path.name)
            all_images.extend(page.regions)

        # 滚动合并（纯文本，无页标记）
        merged_text = page_texts[0]
        # 记录每页在合并文本中的起始位置（用于后续插入页标记）
        # 第一页起始位置为 0
        page_offsets: list[int] = [0]

        for i in range(1, len(page_texts)):
            page_text = page_texts[i]
            result = self.merge_two_pages(
                merged_text, page_text
            )
            # B 的非重叠部分在合并文本中的起始位置
            # 如果有重叠，B 的新内容从 overlap-end 之后开始
            # 用一个唯一标记来定位
            page_offsets.append(
                self._find_page_start(
                    merged_text, result
                )
            )
            merged_text = result.text
            if on_progress is not None:
                on_progress(i, total)

        # 从后往前插入页标记（避免偏移量变化）
        lines = merged_text.splitlines(keepends=True)
        for idx in range(len(page_filenames) - 1, -1, -1):
            marker = f"<!-- page: {page_filenames[idx]} -->\n"
            offset = page_offsets[idx]
            # offset 是字符偏移，转换为行号
            char_count = 0
            insert_line = 0
            for line_idx, line in enumerate(lines):
                if char_count >= offset:
                    insert_line = line_idx
                    break
                char_count += len(line)
            else:
                insert_line = len(lines)
            lines.insert(insert_line, marker)

        return MergedDocument(
            markdown="".join(lines).rstrip("\n"),
            images=all_images,
        )

    def _rewrite_image_refs(self, page: PageOCR) -> str:
        """重写图片引用，不添加页标记。"""
        stem = page.image_path.stem
        text = page.cleaned_text or page.raw_text
        return re.sub(
            r"!\[([^\]]*)\]\(images/",
            rf"![\1]({stem}_OCR/images/",
            text,
        )

    @staticmethod
    def _find_page_start(
        text_a: str,
        result: MergeResult,
    ) -> int:
        """找到 B 页内容在合并文本中的起始字符偏移。"""
        if result.overlap_lines == 0:
            # 无重叠：B 在 A 之后（加上分隔符）
            return len(text_a) + 2  # "\n\n"
        # 有重叠：A 的非重叠部分 + 重叠区域之后就是 B 的新内容
        lines_a = text_a.splitlines()
        overlap_start = len(lines_a) - result.overlap_lines
        before_text = "\n".join(lines_a[:overlap_start])
        overlap_text = "\n".join(lines_a[overlap_start:])
        # B 新内容起始 = before + \n + overlap + \n
        return len(before_text) + 1 + len(overlap_text) + 1
