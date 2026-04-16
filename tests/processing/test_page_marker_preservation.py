# Copyright 2026 @lyty1997
# Licensed under the Apache License, Version 2.0

"""测试 page marker 在去重合并时的保留逻辑"""

from docrestore.pipeline.config import DedupConfig
from docrestore.processing.dedup import PageDeduplicator


def test_page_marker_preserved_when_content_overlaps() -> None:
    """当页面内容重叠时，page marker 必须保留"""
    dedup = PageDeduplicator(DedupConfig())

    text_a = """<!-- page: page1.jpg -->
#### 1.1. Section A
Content from page 1
More content"""

    text_b = """<!-- page: page2.jpg -->
#### 1.1. Section A
Content from page 1
More content
#### 1.2. Section B
New content from page 2"""

    result = dedup.merge_two_pages(text_a, text_b)

    # page2 的 marker 必须存在
    assert "<!-- page: page2.jpg -->" in result.text
    # page1 的 marker 也应该存在
    assert "<!-- page: page1.jpg -->" in result.text
    # 新内容必须保留
    assert "Section B" in result.text
    assert "New content from page 2" in result.text


def test_page_marker_preserved_when_fully_overlaps() -> None:
    """即使整个页面内容都重叠，page marker 也必须保留"""
    dedup = PageDeduplicator(DedupConfig())

    text_a = """<!-- page: page1.jpg -->
Line 1
Line 2
Line 3"""

    text_b = """<!-- page: page2.jpg -->
Line 2
Line 3"""

    result = dedup.merge_two_pages(text_a, text_b)

    # 两个 marker 都必须存在
    assert "<!-- page: page1.jpg -->" in result.text
    assert "<!-- page: page2.jpg -->" in result.text
