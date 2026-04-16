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

"""parse_doc_boundaries / extract_first_heading 单元测试"""

from __future__ import annotations

from docrestore.llm.prompts import extract_first_heading, parse_doc_boundaries


class TestParseDocBoundaries:
    """parse_doc_boundaries 测试"""

    def test_no_boundary(self) -> None:
        """无边界标记时返回空列表"""
        md = "# 标题\n\n正文内容\n<!-- page: a.jpg -->"
        cleaned, boundaries = parse_doc_boundaries(md)
        assert boundaries == []
        assert "标题" in cleaned

    def test_single_boundary(self) -> None:
        """解析单个 DOC_BOUNDARY 标记"""
        md = (
            "第一篇内容\n"
            '<!-- DOC_BOUNDARY: {"after_page":"p3.jpg",'
            '"new_title":"第二篇文档"} -->\n'
            "第二篇内容"
        )
        cleaned, boundaries = parse_doc_boundaries(md)
        assert len(boundaries) == 1
        assert boundaries[0].after_page == "p3.jpg"
        assert boundaries[0].new_title == "第二篇文档"
        assert "DOC_BOUNDARY" not in cleaned

    def test_multiple_boundaries(self) -> None:
        """解析多个 DOC_BOUNDARY 标记"""
        md = (
            "文档A\n"
            '<!-- DOC_BOUNDARY: {"after_page":"p2.jpg","new_title":"B"} -->\n'
            "文档B\n"
            '<!-- DOC_BOUNDARY: {"after_page":"p5.jpg","new_title":"C"} -->\n'
            "文档C"
        )
        cleaned, boundaries = parse_doc_boundaries(md)
        assert len(boundaries) == 2
        assert boundaries[0].after_page == "p2.jpg"
        assert boundaries[1].new_title == "C"
        assert cleaned.count("DOC_BOUNDARY") == 0

    def test_malformed_json_ignored(self) -> None:
        """JSON 格式错误的标记被忽略"""
        md = (
            "内容\n"
            "<!-- DOC_BOUNDARY: {malformed json} -->\n"
            '<!-- DOC_BOUNDARY: {"after_page":"ok.jpg","new_title":"OK"} -->\n'
        )
        cleaned, boundaries = parse_doc_boundaries(md)
        assert len(boundaries) == 1
        assert boundaries[0].after_page == "ok.jpg"

    def test_missing_after_page_ignored(self) -> None:
        """缺少 after_page 字段的标记被忽略"""
        md = '<!-- DOC_BOUNDARY: {"new_title":"只有标题"} -->\n'
        _, boundaries = parse_doc_boundaries(md)
        assert boundaries == []

    def test_whitespace_tolerance(self) -> None:
        """标记内空白容错"""
        md = (
            '<!--  DOC_BOUNDARY:  {"after_page":"x.jpg",'
            '"new_title":"Y"}  -->'
        )
        _, boundaries = parse_doc_boundaries(md)
        assert len(boundaries) == 1
        assert boundaries[0].after_page == "x.jpg"


class TestExtractFirstHeading:
    """extract_first_heading 测试"""

    def test_simple_heading(self) -> None:
        """提取第一个一级标题"""
        md = "# Linux 开发指南\n\n## 第一章"
        assert extract_first_heading(md) == "Linux 开发指南"

    def test_no_heading(self) -> None:
        """无标题时返回空字符串"""
        md = "正文内容\n没有标题"
        assert extract_first_heading(md) == ""

    def test_ignores_lower_headings(self) -> None:
        """只提取 # 一级标题，## 等不算"""
        md = "## 二级标题\n### 三级标题\n# 真正的标题"
        assert extract_first_heading(md) == "真正的标题"

    def test_strips_whitespace(self) -> None:
        """标题前后空白被去除"""
        md = "#   带空格的标题   \n"
        assert extract_first_heading(md) == "带空格的标题"

    def test_first_heading_wins(self) -> None:
        """多个一级标题取第一个"""
        md = "# 第一个\n# 第二个"
        assert extract_first_heading(md) == "第一个"
