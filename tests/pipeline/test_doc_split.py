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

"""Pipeline 文档拆分逻辑测试（_split_by_doc_boundaries）"""

from __future__ import annotations

from pathlib import Path

from docrestore.models import MergedDocument, PageOCR, Region
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline


def _make_page(name: str) -> PageOCR:
    """构造测试用 PageOCR"""
    return PageOCR(
        image_path=Path(f"/fake/{name}"),
        image_size=(100, 100),
        raw_text="",
        cleaned_text="内容",
    )


class TestSplitByDocBoundaries:
    """_split_by_doc_boundaries 拆分逻辑测试"""

    def setup_method(self) -> None:
        """创建 Pipeline 实例"""
        self.pipeline = Pipeline(PipelineConfig())

    def test_no_boundary_single_doc(self) -> None:
        """无边界标记时返回单元素列表"""
        md = (
            "<!-- page: a.jpg -->\n# 文档标题\n内容A\n"
            "<!-- page: b.jpg -->\n内容B"
        )
        doc = MergedDocument(markdown=md)
        pages = [_make_page("a.jpg"), _make_page("b.jpg")]

        result = self.pipeline._split_by_doc_boundaries(doc, pages)

        assert len(result) == 1
        title, page_names, sub_doc = result[0]
        assert title == "文档标题"
        assert set(page_names) == {"a.jpg", "b.jpg"}
        assert "内容A" in sub_doc.markdown
        assert "内容B" in sub_doc.markdown

    def test_single_boundary_splits_two(self) -> None:
        """单个边界标记拆分为两篇文档"""
        md = (
            "<!-- page: p1.jpg -->\n# 第一篇\n内容1\n"
            "<!-- page: p2.jpg -->\n内容2\n"
            '<!-- DOC_BOUNDARY: {"after_page":"p2.jpg",'
            '"new_title":"第二篇文档"} -->\n'
            "<!-- page: p3.jpg -->\n# 第二篇文档\n内容3\n"
            "<!-- page: p4.jpg -->\n内容4"
        )
        doc = MergedDocument(markdown=md)
        pages = [
            _make_page("p1.jpg"),
            _make_page("p2.jpg"),
            _make_page("p3.jpg"),
            _make_page("p4.jpg"),
        ]

        result = self.pipeline._split_by_doc_boundaries(doc, pages)

        assert len(result) == 2

        # 第一篇
        title1, names1, doc1 = result[0]
        assert title1 == "第一篇"
        assert "p1.jpg" in names1
        assert "p2.jpg" in names1
        assert "内容1" in doc1.markdown
        assert "内容3" not in doc1.markdown

        # 第二篇
        title2, names2, doc2 = result[1]
        assert title2 == "第二篇文档"
        assert "p3.jpg" in names2
        assert "p4.jpg" in names2
        assert "内容3" in doc2.markdown

    def test_boundary_with_unknown_page_ignored(self) -> None:
        """after_page 不存在时忽略该边界"""
        md = (
            "<!-- page: a.jpg -->\n# 标题\n内容\n"
            '<!-- DOC_BOUNDARY: {"after_page":"nonexist.jpg",'
            '"new_title":"幽灵"} -->\n'
            "<!-- page: b.jpg -->\n更多内容"
        )
        doc = MergedDocument(markdown=md)
        pages = [_make_page("a.jpg"), _make_page("b.jpg")]

        result = self.pipeline._split_by_doc_boundaries(doc, pages)

        # 边界被忽略，仍是单文档
        assert len(result) == 1

    def test_malformed_boundary_ignored(self) -> None:
        """JSON 格式错误的边界标记被忽略"""
        md = (
            "<!-- page: a.jpg -->\n内容\n"
            "<!-- DOC_BOUNDARY: {bad json} -->\n"
            "<!-- page: b.jpg -->\n更多内容"
        )
        doc = MergedDocument(markdown=md)
        pages = [_make_page("a.jpg"), _make_page("b.jpg")]

        result = self.pipeline._split_by_doc_boundaries(doc, pages)
        assert len(result) == 1

    def test_no_page_markers_single_doc(self) -> None:
        """无 page marker 时返回单文档"""
        md = "# 标题\n内容\n没有 page marker"
        doc = MergedDocument(markdown=md)
        pages = [_make_page("a.jpg")]

        result = self.pipeline._split_by_doc_boundaries(doc, pages)
        assert len(result) == 1

    def test_images_filtered_per_subdoc(self) -> None:
        """每个子文档只包含自己引用的图片"""
        md = (
            "<!-- page: a.jpg -->\n![](a_OCR/images/0.jpg)\n"
            '<!-- DOC_BOUNDARY: {"after_page":"a.jpg",'
            '"new_title":"B"} -->\n'
            "<!-- page: b.jpg -->\n![](b_OCR/images/0.jpg)"
        )
        img_a = Region(
            bbox=(0, 0, 10, 10), label="image",
            cropped_path=Path("/out/a_OCR/images/0.jpg"),
        )
        img_b = Region(
            bbox=(0, 0, 10, 10), label="image",
            cropped_path=Path("/out/b_OCR/images/0.jpg"),
        )
        doc = MergedDocument(markdown=md, images=[img_a, img_b])
        pages = [_make_page("a.jpg"), _make_page("b.jpg")]

        result = self.pipeline._split_by_doc_boundaries(doc, pages)

        assert len(result) == 2
        # 第一篇只有 img_a
        assert len(result[0][2].images) == 1
        assert result[0][2].images[0].cropped_path == img_a.cropped_path
        # 第二篇只有 img_b
        assert len(result[1][2].images) == 1
        assert result[1][2].images[0].cropped_path == img_b.cropped_path
