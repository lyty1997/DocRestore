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

"""文档分段器

将长文档按语义边界分段，供 LLM 逐段精修。
"""

from __future__ import annotations

import re

from docrestore.models import Segment


class DocumentSegmenter:
    """将长文档按语义边界分段"""

    def __init__(
        self,
        max_chars_per_segment: int = 12000,
        overlap_lines: int = 5,
    ) -> None:
        self._max_chars = max_chars_per_segment
        self._overlap_lines = overlap_lines

    def segment(self, markdown: str) -> list[Segment]:
        """分段策略：标题优先切分 → 过长时空行二次切分 → overlap 标记包裹。"""
        if not markdown.strip():
            return []

        lines = markdown.splitlines(keepends=True)

        # 如果整体不超限，直接返回单段
        if len(markdown) <= self._max_chars:
            return [
                Segment(
                    text=markdown,
                    start_line=1,
                    end_line=len(lines),
                )
            ]

        # 第一步：按标题切分
        chunks = self._split_by_headings(lines)

        # 第二步：过长的 chunk 按空行二次切分
        refined_chunks: list[list[tuple[int, str]]] = []
        for chunk in chunks:
            chunk_text = "".join(line for _, line in chunk)
            if len(chunk_text) > self._max_chars:
                refined_chunks.extend(
                    self._split_by_blank_lines(chunk)
                )
            else:
                refined_chunks.append(chunk)

        # 第三步：合并过小的 chunk（避免碎片化）
        merged = self._merge_small_chunks(refined_chunks)

        # 第四步：添加 overlap 标记，构造 Segment
        return self._build_segments_with_overlap(
            merged, lines
        )

    def _split_by_headings(
        self, lines: list[str]
    ) -> list[list[tuple[int, str]]]:
        """按 markdown 标题行切分，返回 [(行号, 行内容)] 的列表。"""
        heading_re = re.compile(r"^#{1,6}\s+")
        chunks: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []

        for i, line in enumerate(lines):
            if heading_re.match(line) and current:
                chunks.append(current)
                current = []
            current.append((i + 1, line))  # 行号从 1 开始

        if current:
            chunks.append(current)
        return chunks

    def _split_by_blank_lines(
        self, chunk: list[tuple[int, str]]
    ) -> list[list[tuple[int, str]]]:
        """对过长 chunk 按空行二次切分。"""
        result: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        current_size = 0

        for line_no, line in chunk:
            current.append((line_no, line))
            current_size += len(line)

            is_blank = line.strip() == ""
            if (
                is_blank
                and current_size >= self._max_chars
                and current
            ):
                result.append(current)
                current = []
                current_size = 0

        if current:
            result.append(current)
        return result

    def _merge_small_chunks(
        self, chunks: list[list[tuple[int, str]]]
    ) -> list[list[tuple[int, str]]]:
        """合并过小的相邻 chunk，避免碎片化。"""
        if not chunks:
            return []

        min_size = self._max_chars // 4
        merged: list[list[tuple[int, str]]] = [chunks[0]]

        for chunk in chunks[1:]:
            prev_text = "".join(line for _, line in merged[-1])
            curr_text = "".join(line for _, line in chunk)
            combined_size = len(prev_text) + len(curr_text)
            if (
                len(prev_text) < min_size
                or len(curr_text) < min_size
            ) and combined_size <= self._max_chars:
                merged[-1].extend(chunk)
            else:
                merged.append(chunk)

        return merged

    def _build_segments_with_overlap(
        self,
        chunks: list[list[tuple[int, str]]],
        all_lines: list[str],
    ) -> list[Segment]:
        """为每段添加前后 overlap 上下文，构造 Segment 列表。"""
        segments: list[Segment] = []
        total_lines = len(all_lines)
        overlap = self._overlap_lines

        for idx, chunk in enumerate(chunks):
            start_line = chunk[0][0]
            end_line = chunk[-1][0]
            body = "".join(line for _, line in chunk)

            parts: list[str] = []

            # 前 overlap（非第一段）
            if idx > 0:
                ol_start = max(0, start_line - 1 - overlap)
                ol_end = start_line - 1
                overlap_text = "".join(
                    all_lines[ol_start:ol_end]
                )
                if overlap_text:
                    parts.append(overlap_text.rstrip("\n"))
                    parts.append("\n")

            parts.append(body.rstrip("\n"))

            # 后 overlap（非最后一段）
            if idx < len(chunks) - 1:
                ol_start = end_line
                ol_end = min(total_lines, end_line + overlap)
                overlap_text = "".join(
                    all_lines[ol_start:ol_end]
                )
                if overlap_text:
                    parts.append("\n")
                    parts.append(overlap_text.rstrip("\n"))

            segments.append(
                Segment(
                    text="\n".join(parts)
                    if len(parts) == 1
                    else "".join(parts),
                    start_line=start_line,
                    end_line=end_line,
                )
            )

        return segments
