# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""StreamSegmentExtractor 单元测试。"""

from __future__ import annotations

from docrestore.processing.segmenter import StreamSegmentExtractor


class TestNotEnoughText:
    def test_text_shorter_than_max_returns_none(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=0)
        assert ex.try_extract("short text", offset=0, max_chars=1000) is None

    def test_starts_from_middle_returns_none(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=0)
        text = "a" * 500
        # offset = 300，剩余 200 < 500
        assert ex.try_extract(text, offset=300, max_chars=500) is None


class TestCutPriority:
    """切点优先级：heading > page marker > 空行 > 任意行起始。

    `try_extract` 要求 `text[offset:]` 长度 ≥ `max_chars`，所以 pad 至少
    放到 `max_chars` 之后才会触发切段。下面统一用 max_chars=100 + pad=95
    把窗口落在 tail 区域。
    """

    def test_heading_wins(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=0)
        pad = "x" * 95  # 95 char 全是非换行填充
        tail = "\n\ncontent\n## My Heading\nfoo\n<!-- page: a.jpg -->\nbar\n"
        text = pad + tail  # 总长 ≈ 150，max_chars=100 → 窗口 [80, 120]
        result = ex.try_extract(text, offset=0, max_chars=100)
        assert result is not None
        _, new_offset = result
        heading_pos = text.find("## My Heading")
        assert new_offset == heading_pos

    def test_page_marker_when_no_heading(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=0)
        pad = "x" * 95
        tail = "\nline\n<!-- page: a.jpg -->\nfoo\nbar\nbaz\n"
        text = pad + tail
        result = ex.try_extract(text, offset=0, max_chars=100)
        assert result is not None
        _, new_offset = result
        marker_pos = text.find("<!-- page: a.jpg -->")
        assert new_offset == marker_pos

    def test_blank_line_when_no_heading_or_marker(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=0)
        pad = "x" * 95
        tail = "\nline-1\nline-2\n\nline-3\n"
        text = pad + tail
        result = ex.try_extract(text, offset=0, max_chars=100)
        assert result is not None
        _, new_offset = result
        # 空行起点是 "\n\n" 中第二个 \n 的位置
        blank_pos = text.find("\n\n") + 1
        assert new_offset == blank_pos

    def test_any_line_start_fallback(self) -> None:
        """仅剩普通行起始可选时返回首个。"""
        ex = StreamSegmentExtractor(overlap_lines=0)
        lines = ["normal line " + str(i) + "\n" for i in range(100)]
        text = "".join(lines)
        result = ex.try_extract(text, offset=0, max_chars=500)
        assert result is not None
        _, new_offset = result
        assert 400 <= new_offset <= 600


class TestBackwardOverlap:
    def test_first_segment_no_overlap(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=3)
        text = ("line\n" * 400)  # 2000 字符
        result = ex.try_extract(text, offset=0, max_chars=1000)
        assert result is not None
        seg_text, _ = result
        assert seg_text == text[: len(seg_text)]  # 头部即从 text 开头开始

    def test_subsequent_segment_prepends_prev_tail(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=2)
        lines = [f"line-{i}\n" for i in range(200)]
        text = "".join(lines)  # 每行 ~8 字符，200 行 ~ 1600+ 字符
        r1 = ex.try_extract(text, offset=0, max_chars=800)
        assert r1 is not None
        _, offset1 = r1
        # 记下第一段最后几行
        first_seg_content = text[:offset1]
        first_lines = first_seg_content.rstrip("\n").splitlines()
        expected_overlap = "\n".join(first_lines[-2:])

        r2 = ex.try_extract(text, offset=offset1, max_chars=800)
        assert r2 is not None
        seg2_text, _ = r2
        assert seg2_text.startswith(expected_overlap)

    def test_reset_clears_overlap(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=2)
        text = ("line\n" * 400)
        ex.try_extract(text, offset=0, max_chars=1000)
        ex.reset()
        r2 = ex.try_extract(text, offset=100, max_chars=1000)
        assert r2 is not None
        seg_text, _ = r2
        # reset 后首段不带 overlap
        assert seg_text == text[100:100 + len(seg_text)]


class TestExtractRemaining:
    def test_returns_all_remaining(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=0)
        text = "short remainder"
        seg_text, new_offset = ex.extract_remaining(text, offset=0)
        assert seg_text == text
        assert new_offset == len(text)

    def test_empty_remainder(self) -> None:
        ex = StreamSegmentExtractor(overlap_lines=0)
        seg_text, new_offset = ex.extract_remaining("abc", offset=3)
        assert seg_text == ""
        assert new_offset == 3


class TestEdgeCases:
    def test_no_newlines_in_window_forces_cut(self) -> None:
        """搜索窗口内无换行 → 在 max_chars 处强制切。"""
        ex = StreamSegmentExtractor(overlap_lines=0)
        text = "x" * 2000  # 无任何换行
        result = ex.try_extract(text, offset=0, max_chars=500)
        assert result is not None
        _, new_offset = result
        assert new_offset == 500
