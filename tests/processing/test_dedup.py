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

"""PageDeduplicator 单元测试"""

from __future__ import annotations

from pathlib import Path

from docrestore.models import PageOCR, Region
from docrestore.pipeline.config import DedupConfig
from docrestore.processing.dedup import PageDeduplicator


def _make_dedup(
    threshold: float = 0.8,
    context_lines: int = 3,
    search_ratio: float = 0.3,
) -> PageDeduplicator:
    """创建 PageDeduplicator 实例"""
    return PageDeduplicator(
        DedupConfig(
            similarity_threshold=threshold,
            overlap_context_lines=context_lines,
            search_ratio=search_ratio,
        )
    )


class TestMergeTwoPages:
    """merge_two_pages 测试"""

    def test_overlap_detected(self) -> None:
        """有明确重叠时，重叠只保留一份"""
        text_a = "行1\n行2\n行3\n行4\n行5\n重叠行A\n重叠行B\n重叠行C"
        text_b = "重叠行A\n重叠行B\n重叠行C\n行6\n行7\n行8"
        dedup = _make_dedup(threshold=0.5, search_ratio=0.5)
        result = dedup.merge_two_pages(text_a, text_b)
        assert result.overlap_lines > 0
        assert result.similarity > 0.5
        # 重叠内容只出现一次
        assert result.text.count("重叠行A") == 1
        # 非重叠内容都在
        assert "行1" in result.text
        assert "行8" in result.text

    def test_no_overlap(self) -> None:
        """无重叠时直接拼接"""
        text_a = "完全不同的内容A"
        text_b = "完全不同的内容B"
        dedup = _make_dedup(search_ratio=1.0)
        result = dedup.merge_two_pages(text_a, text_b)
        assert result.overlap_lines == 0
        assert "完全不同的内容A" in result.text
        assert "完全不同的内容B" in result.text

    def test_empty_text(self) -> None:
        """空文本处理"""
        dedup = _make_dedup()
        result = dedup.merge_two_pages("", "内容B")
        assert "内容B" in result.text
        assert result.overlap_lines == 0


class TestMergeAllPages:
    """merge_all_pages 测试"""

    def test_page_markers_inserted(self) -> None:
        """每页头部插入页边界标记"""
        pages = [
            PageOCR(
                image_path=Path("/img/DSC04654.JPG"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="第一页内容",
            ),
            PageOCR(
                image_path=Path("/img/DSC04655.JPG"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="第二页内容",
            ),
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)
        assert "<!-- page: DSC04654.JPG -->" in doc.markdown
        assert "<!-- page: DSC04655.JPG -->" in doc.markdown

    def test_image_reference_rewrite(self) -> None:
        """图片引用重写为 {stem}_OCR/images/N.jpg"""
        pages = [
            PageOCR(
                image_path=Path("/img/DSC04654.JPG"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="文本\n![](images/0.jpg)\n更多文本",
            ),
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)
        assert "![](DSC04654_OCR/images/0.jpg)" in doc.markdown
        assert "![](images/0.jpg)" not in doc.markdown

    def test_regions_collected(self) -> None:
        """所有页的 regions 汇总到 images"""
        r1 = Region(bbox=(0, 0, 10, 10), label="img1")
        r2 = Region(bbox=(20, 20, 30, 30), label="img2")
        pages = [
            PageOCR(
                image_path=Path("/img/DSC04654.JPG"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="页1",
                regions=[r1],
            ),
            PageOCR(
                image_path=Path("/img/DSC04655.JPG"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="页2",
                regions=[r2],
            ),
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)
        assert len(doc.images) == 2

    def test_progress_callback(self) -> None:
        """进度回调被调用"""
        pages = [
            PageOCR(
                image_path=Path(f"/img/DSC{i}.JPG"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text=f"页{i}内容各不相同第{i}段",
            )
            for i in range(3)
        ]
        dedup = _make_dedup()
        progress_calls: list[tuple[int, int]] = []
        dedup.merge_all_pages(
            pages,
            on_progress=lambda c, t: progress_calls.append(
                (c, t)
            ),
        )
        assert len(progress_calls) == 2
        assert progress_calls[0][0] == 1
        assert progress_calls[1][0] == 2

    def test_empty_pages(self) -> None:
        """空页面列表返回空文档"""
        dedup = _make_dedup()
        doc = dedup.merge_all_pages([])
        assert doc.markdown == ""
        assert doc.images == []

    def test_single_page(self) -> None:
        """单页不做合并"""
        pages = [
            PageOCR(
                image_path=Path("/img/DSC04654.JPG"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="唯一一页",
            ),
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)
        assert "唯一一页" in doc.markdown
        assert "<!-- page: DSC04654.JPG -->" in doc.markdown
