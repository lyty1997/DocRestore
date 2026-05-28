# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IncrementalMerger 单元测试。

核心不变量：对相同输入，`IncrementalMerger` 逐页 `add_page` 后 `get_markdown()`
必须与 `PageDeduplicator.merge_all_pages(pages).markdown` 完全一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docrestore.models import PageOCR
from docrestore.pipeline.config import DedupConfig
from docrestore.processing.dedup import (
    IncrementalMerger,
    PageDeduplicator,
)


def _make_page(
    filename: str,
    text: str,
    tmp_path: Path,
) -> PageOCR:
    """构造最简 PageOCR。output_dir 设为 tmp_path/{stem}_OCR 以保证
    _rewrite_image_refs 行为一致。"""
    image_path = tmp_path / filename
    output_dir = tmp_path / f"{image_path.stem}_OCR"
    return PageOCR(
        image_path=image_path,
        image_size=(1000, 1500),
        raw_text=text,
        cleaned_text=text,
        regions=[],
        output_dir=output_dir,
    )


@pytest.fixture
def dedup_config() -> DedupConfig:
    return DedupConfig()


class TestBasicBehavior:
    """初始状态 / 单页 / 属性字段行为。"""

    def test_initial_empty(self, dedup_config: DedupConfig) -> None:
        merger = IncrementalMerger(dedup_config)
        assert merger.get_markdown() == ""
        assert merger.page_count == 0
        assert merger.all_page_names == []
        assert merger.get_all_images() == []
        assert merger.total_length == 0

    def test_single_page(
        self, dedup_config: DedupConfig, tmp_path: Path,
    ) -> None:
        merger = IncrementalMerger(dedup_config)
        page = _make_page("a.jpg", "# Hello\n\ncontent", tmp_path)
        merger.add_page(page)
        md = merger.get_markdown()
        assert "<!-- page: a.jpg -->" in md
        assert "# Hello" in md
        assert merger.page_count == 1
        assert merger.all_page_names == ["a.jpg"]


class TestConsistencyWithBatch:
    """关键不变量：增量合并 ≡ 批量合并。"""

    def test_two_pages_no_overlap(
        self, dedup_config: DedupConfig, tmp_path: Path,
    ) -> None:
        p1 = _make_page("p1.jpg", "# A\n\nfirst page body\n", tmp_path)
        p2 = _make_page("p2.jpg", "## B\n\nsecond page body\n", tmp_path)

        # 增量
        incr = IncrementalMerger(dedup_config)
        incr.add_page(p1)
        incr.add_page(p2)
        incr_md = incr.get_markdown()

        # 批量
        batch_md = PageDeduplicator(dedup_config).merge_all_pages(
            [p1, p2],
        ).markdown

        assert incr_md == batch_md

    def test_multi_pages_with_overlap(
        self, dedup_config: DedupConfig, tmp_path: Path,
    ) -> None:
        """构造首尾重叠的 3 页，确保 merge_two_pages 做出去重，
        两种合并方式最终结果仍一致。"""
        p1 = _make_page(
            "p1.jpg",
            "line 1\nline 2\nline 3\nline 4\nline 5\n"
            "shared-a\nshared-b\nshared-c\nshared-d\n",
            tmp_path,
        )
        p2 = _make_page(
            "p2.jpg",
            "shared-a\nshared-b\nshared-c\nshared-d\n"
            "middle 1\nmiddle 2\nmiddle 3\nmiddle 4\n"
            "shared-x\nshared-y\nshared-z\nshared-w\n",
            tmp_path,
        )
        p3 = _make_page(
            "p3.jpg",
            "shared-x\nshared-y\nshared-z\nshared-w\n"
            "tail 1\ntail 2\ntail 3\n",
            tmp_path,
        )

        incr = IncrementalMerger(dedup_config)
        for p in (p1, p2, p3):
            incr.add_page(p)
        incr_md = incr.get_markdown()

        batch_md = PageDeduplicator(dedup_config).merge_all_pages(
            [p1, p2, p3],
        ).markdown

        assert incr_md == batch_md
        assert incr.page_count == 3
        assert incr.all_page_names == ["p1.jpg", "p2.jpg", "p3.jpg"]

    def test_image_refs_rewritten(
        self, dedup_config: DedupConfig, tmp_path: Path,
    ) -> None:
        """markdown 图片引用 ![](images/0.jpg) 必须被重写为
        ![]({stem}_OCR/images/0.jpg)，与批量版一致。"""
        body = "## Title\n\n![](images/0.jpg)\n\nparagraph"
        page = _make_page("x.jpg", body, tmp_path)

        incr = IncrementalMerger(dedup_config)
        incr.add_page(page)
        md = incr.get_markdown()
        assert "x_OCR/images/0.jpg" in md
        assert "(images/0.jpg)" not in md


class TestTextAfter:
    def test_get_text_after(
        self, dedup_config: DedupConfig, tmp_path: Path,
    ) -> None:
        merger = IncrementalMerger(dedup_config)
        merger.add_page(_make_page("a.jpg", "body-a", tmp_path))
        full = merger.get_markdown()
        assert merger.get_text_after(0) == full
        assert merger.get_text_after(len(full)) == ""
        assert merger.get_text_after(5) == full[5:]
