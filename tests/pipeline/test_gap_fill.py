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

"""Pipeline gap fill 阶段测试"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.models import Gap, MergedDocument
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline


def _make_pipeline(
    enable_gap_fill: bool = True,
) -> Pipeline:
    """构造带 mock 引擎的 Pipeline。"""
    config = PipelineConfig(
        llm=LLMConfig(
            model="test-model",
            enable_gap_fill=enable_gap_fill,
        ),
    )
    return Pipeline(config)


def _make_doc_with_markers(pages: list[str]) -> str:
    """构造带 page marker 的 markdown。"""
    parts: list[str] = []
    for page in pages:
        parts.append(f"<!-- page: {page} -->")
        parts.append(f"{page} 的内容")
    return "\n".join(parts)


def _make_gap(
    after_image: str = "page1.jpg",
    context_before: str = "前文",
    context_after: str = "后文",
) -> Gap:
    """构造测试用 Gap。"""
    return Gap(
        after_image=after_image,
        context_before=context_before,
        context_after=context_after,
    )


def _make_mock_ocr_engine(
    reocr_result: str = "re-OCR 文本",
    has_reocr: bool = True,
) -> MagicMock:
    """构造支持 reocr_page 的 mock OCR 引擎。"""
    engine = MagicMock()
    if has_reocr:
        engine.reocr_page = AsyncMock(return_value=reocr_result)
    else:
        # 不设置 reocr_page 属性，模拟不支持的引擎
        if hasattr(engine, "reocr_page"):
            delattr(engine, "reocr_page")
    return engine


def _make_mock_refiner(
    fill_result: str = "补充的内容",
    has_fill_gap: bool = True,
) -> MagicMock:
    """构造支持 fill_gap 的 mock refiner。"""
    refiner = MagicMock()
    if has_fill_gap:
        refiner.fill_gap = AsyncMock(return_value=fill_result)
    else:
        if hasattr(refiner, "fill_gap"):
            delattr(refiner, "fill_gap")
    return refiner


class TestFillGaps:
    """Pipeline._fill_gaps() + _maybe_fill_gaps() 测试"""

    @pytest.mark.asyncio
    async def test_content_inserted_at_correct_position(
        self,
    ) -> None:
        """填充内容应插入到下一页 marker 之前，
        且 fill_gap 必须拿到 gap.after_image 的 re-OCR 文本作为当前页，
        gap 之后那一页的 re-OCR 文本作为下一页上下文。"""
        pipeline = _make_pipeline()
        engine = _make_mock_ocr_engine()

        async def _reocr_by_name(path: Path) -> str:
            return f"reocr-of-{path.name}"

        engine.reocr_page = AsyncMock(side_effect=_reocr_by_name)
        refiner = _make_mock_refiner(fill_result="缺失段落")
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        markdown = _make_doc_with_markers(
            ["page1.jpg", "page2.jpg", "page3.jpg"]
        )
        doc = MergedDocument(markdown=markdown)
        gap = _make_gap(after_image="page1.jpg")

        page_map = {
            "page1.jpg": Path("/imgs/page1.jpg"),
            "page2.jpg": Path("/imgs/page2.jpg"),
            "page3.jpg": Path("/imgs/page3.jpg"),
        }
        page_order = ["page1.jpg", "page2.jpg", "page3.jpg"]

        result_doc, filled = await pipeline._fill_gaps(
            doc, [gap], page_map, page_order,
            None, refiner, lambda *_a: None,
        )

        assert filled == 1
        assert gap.filled is True
        assert gap.filled_content == "缺失段落"
        # 内容应在 page2 marker 之前、page1 marker 之后
        md = result_doc.markdown
        page1_marker = "<!-- page: page1.jpg -->"
        page2_marker = "<!-- page: page2.jpg -->"
        page3_marker = "<!-- page: page3.jpg -->"
        p1_pos = md.index(page1_marker)
        p2_pos = md.index(page2_marker)
        p3_pos = md.index(page3_marker)
        content_pos = md.index("缺失段落")
        assert p1_pos < content_pos < p2_pos < p3_pos

        # fill_gap 签名：(gap, current_text, next_page_text, next_page_name)
        refiner.fill_gap.assert_awaited_once()
        call_args = refiner.fill_gap.await_args
        assert call_args.args[0] is gap
        assert call_args.args[1] == "reocr-of-page1.jpg"
        assert call_args.args[2] == "reocr-of-page2.jpg"
        assert call_args.args[3] == "page2.jpg"

    @pytest.mark.asyncio
    async def test_unknown_after_image_skipped(self) -> None:
        """after_image 不在 page_map 中时跳过。"""
        pipeline = _make_pipeline()
        refiner = _make_mock_refiner()

        doc = MergedDocument(markdown="some text")
        gap = _make_gap(after_image="unknown.jpg")

        result_doc, filled = await pipeline._fill_gaps(
            doc, [gap], {}, [],
            None, refiner, lambda *_a: None,
        )

        assert filled == 0
        assert gap.filled is False

    @pytest.mark.asyncio
    async def test_reocr_exception_skips_gap(self) -> None:
        """re-OCR 异常时跳过该 gap，不崩溃。"""
        pipeline = _make_pipeline()
        engine = _make_mock_ocr_engine()
        engine.reocr_page = AsyncMock(
            side_effect=RuntimeError("GPU 错误")
        )
        refiner = _make_mock_refiner()
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        doc = MergedDocument(
            markdown=_make_doc_with_markers(["p1.jpg"])
        )
        gap = _make_gap(after_image="p1.jpg")

        result_doc, filled = await pipeline._fill_gaps(
            doc, [gap],
            {"p1.jpg": Path("/imgs/p1.jpg")},
            ["p1.jpg"],
            None, refiner, lambda *_a: None,
        )

        assert filled == 0
        assert gap.filled is False

    @pytest.mark.asyncio
    async def test_llm_empty_result_not_filled(self) -> None:
        """LLM 返回空字符串时 gap 不标记为 filled。"""
        pipeline = _make_pipeline()
        engine = _make_mock_ocr_engine()
        refiner = _make_mock_refiner(fill_result="")
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        doc = MergedDocument(
            markdown=_make_doc_with_markers(["p1.jpg", "p2.jpg"])
        )
        gap = _make_gap(after_image="p1.jpg")

        result_doc, filled = await pipeline._fill_gaps(
            doc, [gap],
            {"p1.jpg": Path("/p1.jpg"), "p2.jpg": Path("/p2.jpg")},
            ["p1.jpg", "p2.jpg"],
            None, refiner, lambda *_a: None,
        )

        assert filled == 0
        assert gap.filled is False

    @pytest.mark.asyncio
    async def test_reocr_cache_avoids_duplicate_calls(
        self,
    ) -> None:
        """同一页多个 gap：每页只 re-OCR 一次，第二个 gap 的 fill_gap
        必须收到与第一次相同（且唯一）的文本，证明命中的是缓存而非重新 OCR。

        用每次调用返回递增编号的 side_effect：
        - 若缓存生效 → 每路径仅执行一次，两个 gap 的 fill_gap 收到同一文本
        - 若缓存失效 → 每次调用产出不同编号，两个 gap 的 fill_gap 文本不同
        """
        pipeline = _make_pipeline()
        engine = _make_mock_ocr_engine()

        ocr_call_log: list[Path] = []

        async def _reocr_unique(path: Path) -> str:
            ocr_call_log.append(path)
            # 编号自 1 起：缓存命中不会再触发 side_effect
            return f"OCR-{path.name}-#{len(ocr_call_log)}"

        engine.reocr_page = AsyncMock(side_effect=_reocr_unique)
        refiner = _make_mock_refiner(fill_result="补充")
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        doc = MergedDocument(
            markdown=_make_doc_with_markers(
                ["p1.jpg", "p2.jpg"]
            )
        )
        gap1 = _make_gap(after_image="p1.jpg", context_before="a")
        gap2 = _make_gap(after_image="p1.jpg", context_before="b")

        p1_path = Path("/p1.jpg")
        p2_path = Path("/p2.jpg")
        page_map = {"p1.jpg": p1_path, "p2.jpg": p2_path}
        page_order = ["p1.jpg", "p2.jpg"]

        await pipeline._fill_gaps(
            doc, [gap1, gap2], page_map, page_order,
            None, refiner, lambda *_a: None,
        )

        # 每个独立路径只 re-OCR 一次
        assert ocr_call_log.count(p1_path) == 1
        assert ocr_call_log.count(p2_path) == 1
        assert engine.reocr_page.await_count == 2  # = 独立页数

        # 两个 gap 都触发 fill_gap，但 current/next 文本必须相同（命中缓存）
        assert refiner.fill_gap.await_count == 2
        first, second = refiner.fill_gap.await_args_list
        # fill_gap(gap, current_text, next_page_text, next_page_name)
        assert first.args[1] == second.args[1]
        assert first.args[2] == second.args[2]
        assert first.args[3] == "p2.jpg"
        # 且文本来自那仅有的两次 OCR 产出（不是第 3、4 次）
        assert first.args[1] == "OCR-p1.jpg-#1"
        assert first.args[2] == "OCR-p2.jpg-#2"

    @pytest.mark.asyncio
    async def test_enable_gap_fill_false_skips(self) -> None:
        """enable_gap_fill=False 时跳过整个阶段。"""
        pipeline = _make_pipeline(enable_gap_fill=False)
        engine = _make_mock_ocr_engine()
        refiner = _make_mock_refiner()
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        from docrestore.models import PageOCR

        pages = [
            PageOCR(
                image_path=Path("/p1.jpg"),
                image_size=(100, 100),
                raw_text="text",
            )
        ]

        doc = MergedDocument(markdown="content")
        gap = _make_gap(after_image="p1.jpg")

        result = await pipeline._maybe_fill_gaps(
            doc, [gap], pages, Path("/out"),
            None, None, lambda *_a: None,
        )

        # 直接返回原文档
        assert result.markdown == "content"
        engine.reocr_page.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_reocr_method_skips(self) -> None:
        """OCR 引擎不支持 reocr_page 时跳过。"""
        pipeline = _make_pipeline()
        engine = _make_mock_ocr_engine(has_reocr=False)
        refiner = _make_mock_refiner()
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        from docrestore.models import PageOCR

        pages = [
            PageOCR(
                image_path=Path("/p1.jpg"),
                image_size=(100, 100),
                raw_text="text",
            )
        ]

        doc = MergedDocument(markdown="content")
        gap = _make_gap(after_image="p1.jpg")

        result = await pipeline._maybe_fill_gaps(
            doc, [gap], pages, Path("/out"),
            None, None, lambda *_a: None,
        )

        assert result.markdown == "content"

    @pytest.mark.asyncio
    async def test_last_page_gap_appends_to_end(self) -> None:
        """最后一页的 gap 填充内容追加到文档末尾。"""
        pipeline = _make_pipeline()
        engine = _make_mock_ocr_engine()
        refiner = _make_mock_refiner(fill_result="末尾补充")
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        doc = MergedDocument(
            markdown=_make_doc_with_markers(["last.jpg"])
        )
        gap = _make_gap(after_image="last.jpg")

        result_doc, filled = await pipeline._fill_gaps(
            doc, [gap],
            {"last.jpg": Path("/last.jpg")},
            ["last.jpg"],
            None, refiner, lambda *_a: None,
        )

        assert filled == 1
        assert result_doc.markdown.rstrip().endswith("末尾补充")
