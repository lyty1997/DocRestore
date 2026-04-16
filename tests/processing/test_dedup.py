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
from docrestore.processing.dedup import PageDeduplicator, strip_repeated_lines


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
                image_path=Path("/img/page1.jpg"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="第一页内容",
            ),
            PageOCR(
                image_path=Path("/img/page2.jpg"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="第二页内容",
            ),
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)
        assert "<!-- page: page1.jpg -->" in doc.markdown
        assert "<!-- page: page2.jpg -->" in doc.markdown

    def test_image_reference_rewrite(self) -> None:
        """图片引用重写为 {stem}_OCR/images/N.jpg"""
        pages = [
            PageOCR(
                image_path=Path("/img/page1.jpg"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="文本\n![](images/0.jpg)\n更多文本",
            ),
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)
        assert "![](page1_OCR/images/0.jpg)" in doc.markdown
        assert "![](images/0.jpg)" not in doc.markdown

    def test_regions_collected(self) -> None:
        """所有页的 regions 汇总到 images"""
        r1 = Region(bbox=(0, 0, 10, 10), label="img1")
        r2 = Region(bbox=(20, 20, 30, 30), label="img2")
        pages = [
            PageOCR(
                image_path=Path("/img/page1.jpg"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="页1",
                regions=[r1],
            ),
            PageOCR(
                image_path=Path("/img/page2.jpg"),
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
                image_path=Path(f"/img/page{i}.jpg"),
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
                image_path=Path("/img/page1.jpg"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text="唯一一页",
            ),
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)
        assert "唯一一页" in doc.markdown
        assert "<!-- page: page1.jpg -->" in doc.markdown

    def test_page_marker_order_preserved(self) -> None:
        """多页合并后 page marker 顺序与输入一致"""
        pages = [
            PageOCR(
                image_path=Path(f"/img/page{i}.jpg"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text=f"独立内容第{i}段不会重复ABC{i}XYZ",
            )
            for i in range(5)
        ]
        dedup = _make_dedup()
        doc = dedup.merge_all_pages(pages)

        # 提取所有 page marker 的文件名
        import re
        markers = re.findall(
            r"<!-- page: (page\d+\.jpg) -->", doc.markdown,
        )
        expected = [f"page{i}.jpg" for i in range(5)]
        assert markers == expected

    def test_false_overlap_does_not_reorder_markers(
        self,
    ) -> None:
        """相似内容误判重叠时，page marker 仍保持正确顺序"""
        # 模拟 OCR 退化：多页产生相似垃圾文本
        pages = [
            PageOCR(
                image_path=Path(f"/img/page{i}.jpg"),
                image_size=(100, 100),
                raw_text="",
                cleaned_text=(
                    f"性能优化 性能优化 性能优化\n"
                    f"TH1520 诊断手册\n"
                    f"独立内容行{i}"
                ),
            )
            for i in range(4)
        ]
        dedup = _make_dedup(search_ratio=0.7)
        doc = dedup.merge_all_pages(pages)

        import re
        markers = re.findall(
            r"<!-- page: (page\d+\.jpg) -->", doc.markdown,
        )
        expected = [f"page{i}.jpg" for i in range(4)]
        assert markers == expected


# --- strip_repeated_lines 测试 ---


def _make_page(text: str, name: str = "page.jpg") -> PageOCR:
    """创建用于频率过滤测试的 PageOCR。"""
    return PageOCR(
        image_path=Path(f"/img/{name}"),
        image_size=(100, 100),
        raw_text="",
        cleaned_text=text,
        output_dir=Path(f"/out/{name.split('.')[0]}_OCR"),
    )


def _default_dedup_config(**kwargs: object) -> DedupConfig:
    """创建带默认频率过滤参数的 DedupConfig。"""
    defaults: dict[str, object] = {
        "repeated_line_threshold": 0.5,
        "repeated_line_min_pages": 4,
        "repeated_line_min_block": 3,
    }
    defaults.update(kwargs)
    return DedupConfig(**defaults)  # type: ignore[arg-type]


class TestStripRepeatedLines:
    """跨页频率过滤测试"""

    def test_sidebar_removed(self) -> None:
        """侧栏导航在所有页面重复出现 → 被移除。"""
        sidebar = "首页\n目录\n第一章\n第二章\n第三章"
        pages = [
            _make_page(f"{sidebar}\n正文内容_{i}\n独特段落_{i}")
            for i in range(6)
        ]
        config = _default_dedup_config()
        strip_repeated_lines(pages, config)

        for page in pages:
            assert "首页" not in page.cleaned_text
            assert "目录" not in page.cleaned_text
            assert "第三章" not in page.cleaned_text

    def test_main_content_preserved(self) -> None:
        """每页独特的正文不被误删。"""
        sidebar = "导航A\n导航B\n导航C\n导航D"
        pages = [
            _make_page(f"{sidebar}\n独特正文_{i}\n另一段_{i}")
            for i in range(6)
        ]
        config = _default_dedup_config()
        strip_repeated_lines(pages, config)

        for i, page in enumerate(pages):
            assert f"独特正文_{i}" in page.cleaned_text
            assert f"另一段_{i}" in page.cleaned_text

    def test_few_pages_skip(self) -> None:
        """页数不足时跳过过滤（样本不可靠）。"""
        sidebar = "导航A\n导航B\n导航C\n导航D"
        pages = [
            _make_page(f"{sidebar}\n正文_{i}")
            for i in range(3)
        ]
        config = _default_dedup_config(repeated_line_min_pages=4)
        strip_repeated_lines(pages, config)

        # 未过滤，侧栏仍在
        for page in pages:
            assert "导航A" in page.cleaned_text

    def test_isolated_repeated_line_kept(self) -> None:
        """孤立的单行重复不被删除（min_block 保护）。"""
        pages = [
            _make_page(
                f"正文段落1_{i}\n重复短语\n正文段落2_{i}\n其他内容_{i}"
            )
            for i in range(6)
        ]
        config = _default_dedup_config(repeated_line_min_block=3)
        strip_repeated_lines(pages, config)

        # "重复短语"是孤立的 1 行，不满足 min_block=3，保留
        for page in pages:
            assert "重复短语" in page.cleaned_text

    def test_already_cropped_no_effect(self) -> None:
        """已手动裁剪（无侧栏）的照片不受影响。"""
        pages = [
            _make_page(f"纯正文内容_{i}\n段落A_{i}\n段落B_{i}")
            for i in range(6)
        ]
        originals = [p.cleaned_text for p in pages]
        config = _default_dedup_config()
        strip_repeated_lines(pages, config)

        for page, original in zip(pages, originals, strict=True):
            assert page.cleaned_text == original

    def test_short_lines_ignored(self) -> None:
        """单字符行不参与频率统计。"""
        # "·" 在每页都出现，但长度 ≤1，不应被当作噪声
        pages = [
            _make_page(f"·\n·\n·\n·\n正文内容_{i}")
            for i in range(6)
        ]
        config = _default_dedup_config()
        strip_repeated_lines(pages, config)

        for page in pages:
            assert page.cleaned_text.count("·") == 4

    def test_mixed_sidebar_and_content(self) -> None:
        """侧栏块被移除，夹在中间的正文得以保留。"""
        pages = [
            _make_page(
                "导航1\n导航2\n导航3\n导航4\n导航5\n"
                f"正文标题_{i}\n正文段落_{i}\n"
                "底部链接1\n底部链接2\n底部链接3"
            )
            for i in range(6)
        ]
        config = _default_dedup_config()
        strip_repeated_lines(pages, config)

        for i, page in enumerate(pages):
            # 侧栏块（5行连续）被移除
            assert "导航1" not in page.cleaned_text
            # 正文保留
            assert f"正文标题_{i}" in page.cleaned_text
            assert f"正文段落_{i}" in page.cleaned_text
            # 底部链接（3行连续）也被移除
            assert "底部链接1" not in page.cleaned_text
