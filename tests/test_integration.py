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

"""多页 OCR 数据端到端链路测试

使用真实 OCR 数据，测试：
cleaner → dedup → segmenter → LLM refine（可选）
"""

from __future__ import annotations

import os

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.ocr.base import OCR_RESULT_FILENAME
from docrestore.processing.segmenter import DocumentSegmenter
from docrestore.models import PageOCR, RefineContext
from docrestore.pipeline.config import (
    DedupConfig,
    LLMConfig,
)
from docrestore.processing.cleaner import OCRCleaner
from docrestore.processing.dedup import PageDeduplicator

from .conftest import TEST_IMAGE_DIR, TEST_STEMS, get_test_image_path

_GLM_API_KEY = os.environ.get("GLM_API_KEY", "")


@pytest.mark.usefixtures("require_ocr_data")
class TestMultiPagePipeline:
    """多页数据链路测试"""

    @pytest.mark.asyncio
    async def test_clean_all_pages(self) -> None:
        """清洗多页数据，验证 cleaned_text 非空"""
        assert TEST_IMAGE_DIR is not None
        stems = TEST_STEMS[:4]
        cleaner = OCRCleaner()
        pages: list[PageOCR] = []
        for stem in stems:
            page = PageOCR(
                image_path=get_test_image_path(TEST_IMAGE_DIR, stem),
                image_size=(1603, 1720),
                raw_text="",
                output_dir=TEST_IMAGE_DIR / f"{stem}_OCR",
            )
            await cleaner.clean(page)
            pages.append(page)

        for page in pages:
            assert page.cleaned_text != "", (
                f"{page.image_path.name} cleaned_text 为空"
            )

        # 第一页去重效果检查
        raw_text = (
            TEST_IMAGE_DIR / f"{stems[0]}_OCR" / OCR_RESULT_FILENAME
        ).read_text()
        raw_count = raw_text.count("- 0 -")
        cleaned_count = pages[0].cleaned_text.count("- 0 -")
        print(
            f"\n{stems[0]} '- 0 -' 出现次数: "
            f"原始={raw_count}, 清洗后={cleaned_count}"
        )

    @pytest.mark.asyncio
    async def test_clean_and_merge(self) -> None:
        """清洗 + 去重合并，验证重叠检测"""
        assert TEST_IMAGE_DIR is not None
        stems = TEST_STEMS[:4]
        cleaner = OCRCleaner()
        pages: list[PageOCR] = []
        for stem in stems:
            page = PageOCR(
                image_path=get_test_image_path(TEST_IMAGE_DIR, stem),
                image_size=(1603, 1720),
                raw_text="",
                output_dir=TEST_IMAGE_DIR / f"{stem}_OCR",
            )
            await cleaner.clean(page)
            pages.append(page)

        dedup = PageDeduplicator(DedupConfig())
        doc = dedup.merge_all_pages(pages)

        assert doc.markdown != ""
        # 验证页标记包含动态文件名（后缀大小写不强绑定）
        first_name = get_test_image_path(
            TEST_IMAGE_DIR, stems[0]
        ).name
        last_name = get_test_image_path(
            TEST_IMAGE_DIR, stems[-1]
        ).name
        assert f"<!-- page: {first_name} -->" in doc.markdown
        assert f"<!-- page: {last_name} -->" in doc.markdown

        print(f"\n合并后文档长度: {len(doc.markdown)} 字符")
        print(f"页标记数量: {doc.markdown.count('<!-- page:')}")
        print("\n=== 合并结果前 500 字符 ===")
        print(doc.markdown[:500])
        print("=== ... ===")

    @pytest.mark.asyncio
    async def test_clean_merge_and_segment(self) -> None:
        """清洗 + 合并 + 分段"""
        assert TEST_IMAGE_DIR is not None
        stems = TEST_STEMS[:4]
        cleaner = OCRCleaner()
        pages: list[PageOCR] = []
        for stem in stems:
            page = PageOCR(
                image_path=get_test_image_path(TEST_IMAGE_DIR, stem),
                image_size=(1603, 1720),
                raw_text="",
                output_dir=TEST_IMAGE_DIR / f"{stem}_OCR",
            )
            await cleaner.clean(page)
            pages.append(page)

        dedup = PageDeduplicator(DedupConfig())
        doc = dedup.merge_all_pages(pages)

        segmenter = DocumentSegmenter(
            max_chars_per_segment=12000, overlap_lines=5
        )
        segments = segmenter.segment(doc.markdown)

        print(f"\n分段数量: {len(segments)}")
        for i, seg in enumerate(segments):
            print(
                f"  段{i + 1}: {len(seg.text)} 字符, "
                f"行 {seg.start_line}-{seg.end_line}"
            )

        assert len(segments) >= 1
        for seg in segments:
            assert len(seg.text) <= 15000

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _GLM_API_KEY,
        reason="GLM_API_KEY 未设置",
    )
    async def test_full_chain_with_llm(self) -> None:
        """完整链路：清洗 → 合并 → 分段 → LLM 精修"""
        assert TEST_IMAGE_DIR is not None
        stems = TEST_STEMS[:4]
        # 清洗
        cleaner = OCRCleaner()
        pages: list[PageOCR] = []
        for stem in stems:
            page = PageOCR(
                image_path=get_test_image_path(TEST_IMAGE_DIR, stem),
                image_size=(1603, 1720),
                raw_text="",
                output_dir=TEST_IMAGE_DIR / f"{stem}_OCR",
            )
            await cleaner.clean(page)
            pages.append(page)

        # 合并
        dedup = PageDeduplicator(DedupConfig())
        doc = dedup.merge_all_pages(pages)

        # 分段
        segmenter = DocumentSegmenter(
            max_chars_per_segment=12000, overlap_lines=5
        )
        segments = segmenter.segment(doc.markdown)

        # LLM 精修
        config = LLMConfig(
            model="openai/glm-5",
            api_base="https://poloai.top/v1",
            api_key=_GLM_API_KEY,
        )
        refiner = CloudLLMRefiner(config)

        print("\n=== LLM 精修开始 ===")
        all_gaps = []
        for i, seg in enumerate(segments):
            ctx = RefineContext(
                segment_index=i + 1,
                total_segments=len(segments),
                overlap_before="",
                overlap_after="",
            )
            result = await refiner.refine(seg.text, ctx)
            print(
                f"\n--- 段 {i + 1}/{len(segments)} "
                f"({len(result.markdown)} 字符, "
                f"{len(result.gaps)} 个 GAP) ---"
            )
            print(result.markdown[:300])
            if len(result.markdown) > 300:
                print("...")
            all_gaps.extend(result.gaps)

        print(f"\n=== 精修完成，共 {len(all_gaps)} 个 GAP ===")
        for gap in all_gaps:
            print(
                f"  GAP: after={gap.after_image}, "
                f"before={gap.context_before!r}, "
                f"after={gap.context_after!r}"
            )
