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


# ── 流式分段提取器 ────────────────────────────────────────

_HEADING_RE = re.compile(r"^#{1,6}\s+")
_PAGE_MARKER_PREFIX = "<!-- page:"


class StreamSegmentExtractor:
    """流式分段：从增长中的 markdown 增量提取 segment。

    与 `DocumentSegmenter` 的区别：
    - 一次提取一段（`try_extract`）而不是全切
    - `max_chars` 每次调用传入，支持运行时自适应调整（`RateController`）
    - 只做 backward overlap（流式模式下未来文本未知）

    典型用法：
        extractor = StreamSegmentExtractor(overlap_lines=5)
        while True:
            result = extractor.try_extract(current_text, offset, L_star)
            if result is None:
                break  # 文本不够长，等更多页面
            seg_text, offset = result
            refine(seg_text)
    """

    def __init__(self, overlap_lines: int = 5) -> None:
        self._overlap_lines = overlap_lines
        self._prev_tail_lines: list[str] = []

    def try_extract(
        self, full_text: str, offset: int, max_chars: int,
    ) -> tuple[str, int] | None:
        """尝试从 full_text[offset:] 提取一个 segment。

        - full_text[offset:] 长度 < max_chars 返回 None（等更多文本）
        - 否则在 [offset + max_chars*0.8, offset + max_chars*1.2] 内按
          heading > page marker > 空行 > 任意换行 的优先级搜切点
        - 超出 1.2 倍仍无切点 → 在 offset + max_chars 强制切

        返回 (segment_text_with_backward_overlap, new_offset)。
        """
        remaining_len = len(full_text) - offset
        if remaining_len < max_chars:
            return None

        cut_pos = self._find_cut_position(
            full_text, offset, max_chars,
        )
        actual = full_text[offset:cut_pos]
        seg_text = self._compose_with_overlap(actual)
        self._update_tail(actual)
        return seg_text, cut_pos

    def extract_remaining(
        self, full_text: str, offset: int,
    ) -> tuple[str, int]:
        """强制把 full_text[offset:] 作为最后一段返回（可为空串）。

        含 backward overlap。始终返回有效结果。
        """
        actual = full_text[offset:]
        seg_text = self._compose_with_overlap(actual)
        self._update_tail(actual)
        return seg_text, len(full_text)

    def reset(self) -> None:
        """清除 overlap 历史（新文档开始时使用）。"""
        self._prev_tail_lines = []

    # ── 内部实现 ──────────────────────────────────────────

    def _find_cut_position(
        self, text: str, offset: int, max_chars: int,
    ) -> int:
        """在 [offset + 0.8*max, offset + 1.2*max] 搜最优切点。

        切点取 `\\n` 之后的行起始位置（即从 cut_pos 开始是新一行）。
        """
        total = len(text)
        min_cut = offset + int(max_chars * 0.8)
        max_cut = offset + int(max_chars * 1.2)
        if max_cut > total:
            max_cut = total
        if min_cut >= total:
            return total

        line_starts = self._line_starts_in(text, min_cut, max_cut)
        if not line_starts:
            # 搜索窗口内一个换行都没有 → 强制在 max_cut 切
            return offset + max_chars

        cut = self._select_best_cut(text, line_starts)
        return cut if cut >= 0 else offset + max_chars

    def _select_best_cut(
        self, text: str, line_starts: list[int],
    ) -> int:
        """按优先级（heading > page_marker > blank > any）选首个匹配位置。"""
        buckets: dict[str, int] = {
            "heading": -1,
            "page_marker": -1,
            "blank": -1,
            "any": -1,
        }
        for pos in line_starts:
            if buckets["any"] < 0:
                buckets["any"] = pos
            kind = self._classify_line(text, pos)
            if kind is not None and buckets[kind] < 0:
                buckets[kind] = pos
        for key in ("heading", "page_marker", "blank", "any"):
            if buckets[key] >= 0:
                return buckets[key]
        return -1

    @staticmethod
    def _classify_line(text: str, pos: int) -> str | None:
        """判断 text 中 pos 位置所在行属于哪种切点类型。"""
        line_end = text.find("\n", pos)
        line = text[pos:line_end] if line_end != -1 else text[pos:]
        stripped = line.strip()
        if not stripped:
            return "blank"
        if _HEADING_RE.match(line):
            return "heading"
        if stripped.startswith(_PAGE_MARKER_PREFIX):
            return "page_marker"
        return None

    @staticmethod
    def _line_starts_in(
        text: str, from_pos: int, to_pos: int,
    ) -> list[int]:
        """返回 [from_pos, to_pos) 内所有行起始位置（`\\n` 之后的 index）。"""
        starts: list[int] = []
        # 回退到 from_pos 所在行的起始位置（如果 from_pos 不是行首，
        # 则看 from_pos 到 to_pos 之间的下一个 `\n` 之后位置）
        cursor = from_pos
        if cursor > 0 and text[cursor - 1] != "\n":
            nxt = text.find("\n", cursor, to_pos)
            if nxt == -1:
                return []
            cursor = nxt + 1
        while cursor < to_pos:
            starts.append(cursor)
            nxt = text.find("\n", cursor, to_pos)
            if nxt == -1:
                break
            cursor = nxt + 1
        return starts

    def _compose_with_overlap(self, actual_segment: str) -> str:
        """在 actual_segment 头部附加 backward overlap（若有）。"""
        if not self._prev_tail_lines:
            return actual_segment
        overlap = "\n".join(self._prev_tail_lines)
        if actual_segment.startswith("\n"):
            return overlap + actual_segment
        return overlap + "\n" + actual_segment

    def _update_tail(self, actual_segment: str) -> None:
        """记录本段末尾若干行作为下一段 overlap 来源。"""
        if self._overlap_lines <= 0 or not actual_segment:
            self._prev_tail_lines = []
            return
        lines = actual_segment.rstrip("\n").splitlines()
        self._prev_tail_lines = lines[-self._overlap_lines:]
