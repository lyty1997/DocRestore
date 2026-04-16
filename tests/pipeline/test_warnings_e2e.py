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

"""PipelineResult.warnings 端到端聚合测试

验证 Pipeline 在完整流程中正确聚合三类警告到 PipelineResult.warnings：
1. 段级 truncated（refiner.refine 返回 truncated=True）
2. 整篇 final_refine truncated
3. Gap 未被成功补充（filled=False）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.models import Gap, PageOCR, RefinedResult
from docrestore.pipeline.config import (
    LLMConfig,
    PIIConfig,
    PipelineConfig,
)
from docrestore.pipeline.pipeline import Pipeline


def _engine(texts: dict[str, str]) -> MagicMock:
    engine = MagicMock()

    async def _ocr(image_path: Path, _out: Path) -> PageOCR:
        t = texts.get(image_path.name, image_path.name)
        return PageOCR(
            image_path=image_path,
            image_size=(100, 100),
            raw_text=t,
            cleaned_text=t,
        )

    engine.ocr = AsyncMock(side_effect=_ocr)
    engine.reocr_page = AsyncMock(return_value="")
    engine.shutdown = AsyncMock(return_value=None)
    return engine


def _build_dir(root: Path, names: list[str]) -> Path:
    img_dir = root / "imgs"
    img_dir.mkdir()
    for n in names:
        (img_dir / n).write_bytes(b"fake")
    return img_dir


class TestWarningsAggregationE2E:
    """Pipeline.process_many 产出的 PipelineResult.warnings 聚合"""

    @pytest.mark.asyncio
    async def test_segment_truncated_goes_into_warnings(
        self, tmp_path: Path,
    ) -> None:
        """refine 返回 truncated=True → warnings 含"段 N 精修输出疑似被截断"。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(_engine({"a.jpg": "a", "b.jpg": "b"}))

        refiner = MagicMock()

        async def _refine(text: str, _ctx: object) -> object:
            return RefinedResult(
                markdown=text, gaps=[], truncated=True,
            )

        refiner.refine = AsyncMock(side_effect=_refine)
        refiner.final_refine = AsyncMock(
            side_effect=lambda md: RefinedResult(
                markdown=md, gaps=[], truncated=False,
            ),
        )
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        refiner.fill_gap = AsyncMock(return_value="")
        pipeline.set_refiner(refiner)

        img_dir = _build_dir(tmp_path, ["a.jpg", "b.jpg"])
        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        assert len(results) == 1
        warnings = results[0].warnings
        assert any("精修输出疑似被截断" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_final_refine_truncated_adds_warning(
        self, tmp_path: Path,
    ) -> None:
        """final_refine 返回 truncated=True → warnings 含整篇截断提示。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
                enable_final_refine=True,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(_engine({"p.jpg": "content"}))

        refiner = MagicMock()
        refiner.refine = AsyncMock(
            side_effect=lambda text, _c: RefinedResult(
                markdown=text, gaps=[], truncated=False,
            ),
        )
        refiner.final_refine = AsyncMock(
            side_effect=lambda md: RefinedResult(
                markdown=md, gaps=[], truncated=True,
            ),
        )
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        refiner.fill_gap = AsyncMock(return_value="")
        pipeline.set_refiner(refiner)

        img_dir = _build_dir(tmp_path, ["p.jpg"])
        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        warnings = results[0].warnings
        assert any("整篇文档级精修" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_unfilled_gap_adds_warning(
        self, tmp_path: Path,
    ) -> None:
        """gap 未被成功补充（fill_gap 返回空） → warnings 含该 gap 的提示。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=True,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        engine = _engine({"p1.jpg": "a", "p2.jpg": "b"})
        engine.reocr_page = AsyncMock(return_value="reocr text")
        pipeline.set_ocr_engine(engine)

        refiner = MagicMock()

        async def _refine(text: str, _ctx: object) -> object:
            # 在 refine 阶段制造一个 gap
            if "page: p1.jpg" in text:
                return RefinedResult(
                    markdown=text,
                    gaps=[
                        Gap(
                            after_image="p1.jpg",
                            context_before="a",
                            context_after="b",
                        ),
                    ],
                    truncated=False,
                )
            return RefinedResult(
                markdown=text, gaps=[], truncated=False,
            )

        refiner.refine = AsyncMock(side_effect=_refine)
        refiner.final_refine = AsyncMock(
            side_effect=lambda md: RefinedResult(
                markdown=md, gaps=[], truncated=False,
            ),
        )
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        # 返回空字符串 → fill 失败
        refiner.fill_gap = AsyncMock(return_value="")
        pipeline.set_refiner(refiner)

        img_dir = _build_dir(tmp_path, ["p1.jpg", "p2.jpg"])
        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        warnings = results[0].warnings
        assert any(
            "p1.jpg" in w and "未能自动补充" in w
            for w in warnings
        )

    @pytest.mark.asyncio
    async def test_all_three_warning_types_aggregate(
        self, tmp_path: Path,
    ) -> None:
        """段截断 + 整篇截断 + 未补 gap 同时出现 → warnings 三类齐全。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=True,
                enable_final_refine=True,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        engine = _engine({"x.jpg": "x text", "y.jpg": "y text"})
        engine.reocr_page = AsyncMock(return_value="reocr")
        pipeline.set_ocr_engine(engine)

        refiner = MagicMock()

        async def _refine(text: str, _ctx: object) -> object:
            return RefinedResult(
                markdown=text,
                gaps=[
                    Gap(
                        after_image="x.jpg",
                        context_before="x",
                        context_after="y",
                    ),
                ],
                truncated=True,
            )

        refiner.refine = AsyncMock(side_effect=_refine)
        refiner.final_refine = AsyncMock(
            side_effect=lambda md: RefinedResult(
                markdown=md, gaps=[], truncated=True,
            ),
        )
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        refiner.fill_gap = AsyncMock(return_value="")
        pipeline.set_refiner(refiner)

        img_dir = _build_dir(tmp_path, ["x.jpg", "y.jpg"])
        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        warnings = results[0].warnings
        assert any("精修输出疑似被截断" in w for w in warnings)
        assert any("整篇文档级精修" in w for w in warnings)
        assert any("未能自动补充" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_clean_run_produces_no_warnings(
        self, tmp_path: Path,
    ) -> None:
        """无截断无 gap → warnings 为空。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(_engine({"a.jpg": "hello"}))

        refiner = MagicMock()
        refiner.refine = AsyncMock(
            side_effect=lambda text, _c: RefinedResult(
                markdown=text, gaps=[], truncated=False,
            ),
        )
        refiner.final_refine = AsyncMock(
            side_effect=lambda md: RefinedResult(
                markdown=md, gaps=[], truncated=False,
            ),
        )
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        refiner.fill_gap = AsyncMock(return_value="")
        pipeline.set_refiner(refiner)

        img_dir = _build_dir(tmp_path, ["a.jpg"])
        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        assert results[0].warnings == []
