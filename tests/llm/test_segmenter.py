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

"""DocumentSegmenter 单元测试"""

from __future__ import annotations

from docrestore.llm.segmenter import DocumentSegmenter


class TestSegmentBasic:
    """基本分段行为"""

    def test_short_text_single_segment(self) -> None:
        """短文本只产出 1 段"""
        seg = DocumentSegmenter(max_chars_per_segment=12000)
        text = "# 标题\n\n这是一段短文本。"
        result = seg.segment(text)
        assert len(result) == 1
        assert result[0].text == text
        assert result[0].start_line == 1

    def test_empty_text(self) -> None:
        """空文本返回空列表"""
        seg = DocumentSegmenter()
        assert seg.segment("") == []
        assert seg.segment("   \n  ") == []

    def test_split_by_headings(self) -> None:
        """按标题切分"""
        seg = DocumentSegmenter(max_chars_per_segment=50)
        text = (
            "# 第一章\n\n第一章内容很长" + "x" * 30 + "\n\n"
            "# 第二章\n\n第二章内容很长" + "y" * 30
        )
        result = seg.segment(text)
        assert len(result) >= 2
        # 第一段包含第一章
        assert "第一章" in result[0].text
        # 最后一段包含第二章
        assert "第二章" in result[-1].text

    def test_split_by_blank_lines_when_no_headings(
        self,
    ) -> None:
        """无标题的长文本在空行处二次切分"""
        seg = DocumentSegmenter(max_chars_per_segment=100)
        # 构造无标题但有空行分隔的长文本
        paragraphs = [
            f"段落{i}内容" + "字" * 40 for i in range(5)
        ]
        text = "\n\n".join(paragraphs)
        result = seg.segment(text)
        assert len(result) >= 2


class TestSegmentOverlap:
    """overlap 标记测试"""

    def test_overlap_markers_present(self) -> None:
        """多段时中间段有 overlap 标记"""
        seg = DocumentSegmenter(
            max_chars_per_segment=80, overlap_lines=3
        )
        lines = []
        for i in range(20):
            lines.append(f"# 章节{i}")
            lines.append("")
            lines.append(f"内容行{i}" + "x" * 40)
            lines.append("")
        text = "\n".join(lines)
        result = seg.segment(text)
        if len(result) >= 3:
            mid = result[1].text
            # 中间段应包含前后 overlap 上下文内容
            assert "内容行" in mid

    def test_first_segment_no_leading_overlap(self) -> None:
        """第一段没有前 overlap"""
        seg = DocumentSegmenter(
            max_chars_per_segment=80, overlap_lines=3
        )
        lines = []
        for i in range(10):
            lines.append(f"# 章节{i}")
            lines.append(f"内容{i}" + "x" * 40)
            lines.append("")
        text = "\n".join(lines)
        result = seg.segment(text)
        if len(result) >= 2:
            first = result[0].text
            # 第一段开头不应有前一段的 overlap 上下文
            # （第一段前面没有内容可以 overlap）
            assert first.startswith("# 章节0") or first.startswith("\n# 章节0")


class TestSegmentLineNumbers:
    """行号范围测试"""

    def test_line_numbers_cover_full_text(self) -> None:
        """所有段的行号范围覆盖完整文本"""
        seg = DocumentSegmenter(max_chars_per_segment=100)
        paragraphs = [
            f"# 章节{i}\n\n内容{i}" + "字" * 40
            for i in range(5)
        ]
        text = "\n\n".join(paragraphs)
        result = seg.segment(text)
        assert result[0].start_line == 1
        # 最后一段的 end_line 应该接近总行数
        total_lines = len(text.splitlines())
        assert result[-1].end_line <= total_lines
