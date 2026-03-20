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

"""models.py 数据对象单元测试"""

from pathlib import Path

from docrestore.models import (
    Gap,
    MergedDocument,
    MergeResult,
    PageOCR,
    PipelineResult,
    RefineContext,
    RefinedResult,
    Region,
    Segment,
    TaskProgress,
)


class TestRegion:
    """Region dataclass 测试"""

    def test_basic_fields(self) -> None:
        """基本字段赋值"""
        r = Region(bbox=(10, 20, 100, 200), label="image")
        assert r.bbox == (10, 20, 100, 200)
        assert r.label == "image"
        assert r.cropped_path is None

    def test_with_cropped_path(self) -> None:
        """带裁剪路径"""
        p = Path("/data/crop.jpg")
        r = Region(bbox=(0, 0, 50, 50), label="title", cropped_path=p)
        assert r.cropped_path == p


class TestPageOCR:
    """PageOCR dataclass 测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        page = PageOCR(
            image_path=Path("/img/test.jpg"),
            image_size=(1603, 1720),
            raw_text="# 标题\n正文",
        )
        assert page.cleaned_text == ""
        assert page.regions == []
        assert page.output_dir is None
        assert page.has_eos is True

    def test_full_fields(self) -> None:
        """全字段赋值"""
        region = Region(bbox=(0, 0, 100, 100), label="image")
        page = PageOCR(
            image_path=Path("/img/page1.jpg"),
            image_size=(1603, 1720),
            raw_text="raw",
            cleaned_text="cleaned",
            regions=[region],
            output_dir=Path("/out/page1_OCR"),
            has_eos=False,
        )
        assert page.cleaned_text == "cleaned"
        assert len(page.regions) == 1
        assert page.has_eos is False


class TestMergeResult:
    """MergeResult dataclass 测试"""

    def test_fields(self) -> None:
        """字段赋值"""
        mr = MergeResult(text="merged", overlap_lines=5, similarity=0.85)
        assert mr.text == "merged"
        assert mr.overlap_lines == 5
        assert mr.similarity == 0.85


class TestGap:
    """Gap dataclass 测试"""

    def test_fields(self) -> None:
        """字段赋值"""
        g = Gap(
            after_image="page57.jpg",
            context_before="前文最后一句",
            context_after="后文第一句",
        )
        assert g.after_image == "page57.jpg"
        assert g.context_before == "前文最后一句"


class TestMergedDocument:
    """MergedDocument dataclass 测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        doc = MergedDocument(markdown="# 文档")
        assert doc.images == []
        assert doc.gaps == []

    def test_with_images_and_gaps(self) -> None:
        """带图片和缺口"""
        region = Region(bbox=(0, 0, 50, 50), label="fig")
        gap = Gap("page55.jpg", "before", "after")
        doc = MergedDocument(
            markdown="# 文档", images=[region], gaps=[gap]
        )
        assert len(doc.images) == 1
        assert len(doc.gaps) == 1


class TestSegment:
    """Segment dataclass 测试"""

    def test_fields(self) -> None:
        """字段赋值"""
        seg = Segment(text="段落内容", start_line=1, end_line=10)
        assert seg.text == "段落内容"
        assert seg.start_line == 1
        assert seg.end_line == 10


class TestRefineContext:
    """RefineContext dataclass 测试"""

    def test_fields(self) -> None:
        """字段赋值"""
        ctx = RefineContext(
            segment_index=1,
            total_segments=3,
            overlap_before="",
            overlap_after="overlap text",
        )
        assert ctx.segment_index == 1
        assert ctx.overlap_before == ""
        assert ctx.overlap_after == "overlap text"


class TestRefinedResult:
    """RefinedResult dataclass 测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        rr = RefinedResult(markdown="# 精修后")
        assert rr.gaps == []

    def test_with_gaps(self) -> None:
        """带缺口"""
        gap = Gap("page60.jpg", "b", "a")
        rr = RefinedResult(markdown="text", gaps=[gap])
        assert len(rr.gaps) == 1


class TestPipelineResult:
    """PipelineResult dataclass 测试"""

    def test_fields(self) -> None:
        """全字段赋值"""
        pr = PipelineResult(
            output_path=Path("/out/document.md"),
            markdown="# 最终文档",
            images=[Region(bbox=(0, 0, 1, 1), label="img")],
            gaps=[Gap("page70.jpg", "x", "y")],
        )
        assert pr.output_path == Path("/out/document.md")
        assert pr.markdown == "# 最终文档"
        assert len(pr.images) == 1
        assert len(pr.gaps) == 1

    def test_defaults(self) -> None:
        """默认列表"""
        pr = PipelineResult(
            output_path=Path("/out/doc.md"), markdown="text"
        )
        assert pr.images == []
        assert pr.gaps == []


class TestTaskProgress:
    """TaskProgress dataclass 测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        tp = TaskProgress(stage="ocr")
        assert tp.current == 0
        assert tp.total == 0
        assert tp.percent == 0.0
        assert tp.message == ""

    def test_full_fields(self) -> None:
        """全字段赋值"""
        tp = TaskProgress(
            stage="refine",
            current=3,
            total=10,
            percent=30.0,
            message="正在精修第 3 段...",
        )
        assert tp.stage == "refine"
        assert tp.percent == 30.0
