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

    def test_short_tail_head_overlap(self) -> None:
        """短跨页重叠（2-3 行）必须被检测到。

        回归：DDR_适配指南 案例——上页末尾 "也可以在..." + "Usage: ..."
        与下页开头完全一致，旧版 find_longest_match 被 A 中间巧合的
        "Plain Text 复制代码" + 空行重复骗走，导致这 2 行重复保留。
        新版 suffix-prefix 锚定应该精确去重。
        """
        text_a = (
            "前面内容\n\n"
            "Plain Text 复制代码\n\n"  # A 中间的巧合重复
            "一些代码\n\n"
            "Plain Text 复制代码\n\n"  # A 中间再次出现
            "更多代码\n\n"
            "也可以在 Uboot 命令行，通过 stress_test 压测 DDR 读写的正确性\n\n"
            "Usage: stress_test [start end [pattern [iterations]]]"
        )
        text_b = (
            "也可以在 Uboot 命令行，通过 stress_test 压测 DDR 读写的正确性\n\n"
            "Usage: stress_test [start end [pattern [iterations]]]\n\n"
            "Plain Text 复制代码\n\n"
            "1 实际压测代码"
        )
        dedup = _make_dedup(threshold=0.8, search_ratio=0.7)
        result = dedup.merge_two_pages(text_a, text_b)
        # 真正的跨页重叠应被识别
        assert result.overlap_lines > 0
        # 两行跨页重复仅保留一次
        assert result.text.count("也可以在 Uboot 命令行") == 1
        assert result.text.count(
            "Usage: stress_test [start end [pattern [iterations]]]"
        ) == 1
        # B 的新内容完整保留
        assert "1 实际压测代码" in result.text

    def test_middle_coincidence_not_merged(self) -> None:
        """A 中间与 B 头部的巧合重复不应被误判为页面重叠。

        A 的 *尾部* 不包含 B 的头部，但 A 中间恰好有几行与 B 开头相同。
        旧版 find_longest_match 会锁定这个中间匹配导致错误去重 B 的开头。
        新版锚定约束应拒绝这种匹配。
        """
        text_a = (
            "第一段内容\n"
            "巧合行X\n"
            "巧合行Y\n"
            "中间别的内容\n"
            "A 尾部独有的内容\n"
            "A 最后一行"
        )
        text_b = (
            "巧合行X\n"
            "巧合行Y\n"
            "B 独有的新内容\n"
            "B 最后一行"
        )
        dedup = _make_dedup(threshold=0.5, search_ratio=1.0)
        result = dedup.merge_two_pages(text_a, text_b)
        # 不应识别为重叠
        assert result.overlap_lines == 0
        # A、B 完整保留
        assert "A 尾部独有的内容" in result.text
        assert "B 独有的新内容" in result.text
        # 巧合行 A 里一次、B 里一次，合计两次
        assert result.text.count("巧合行X") == 2


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
