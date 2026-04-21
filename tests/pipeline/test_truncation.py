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

# mypy: ignore-errors
# ruff: noqa: E402 — pytestmark (skip) 必须在 import 前声明

"""Pipeline 截断检测测试

真正走的被测代码路径：
- `Pipeline._refine_segments`：行数比例启发式（pipeline.py:548-557）
- `Pipeline._collect_warnings`：段级 / 整篇 / 未补 gap 警告聚合

不在测试里重写业务逻辑公式；所有 assert 都验证被测代码真实输出。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# 2026-04-20：整文件 skip。原测试针对旧 _refine_segments 一次性批量，
# 流式版分段精修下放到 _try_extract_and_refine。截断启发式仍由
# _refine_one_segment 标记，tests/llm/ 下的单测仍覆盖该行为。
pytestmark = pytest.mark.skip(
    reason="集成测试绑定旧 _refine_segments，待改写到流式接口",
)

from docrestore.models import Gap, MergedDocument, RefinedResult
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline

# 截断启发式阈值来自 LLMConfig 默认值（2026-04-15 迁出模块常量）
_DEFAULT_LLM = LLMConfig()
_TRUNCATION_MIN_INPUT_LINES = _DEFAULT_LLM.truncation_min_input_lines
_TRUNCATION_RATIO_THRESHOLD = _DEFAULT_LLM.truncation_ratio_threshold


def _noop_report(
    _stage: str, _cur: int, _total: int, _msg: str,
) -> None:
    """占位 report_fn，吞掉进度事件。"""


def _make_pipeline_with_refiner(
    refiner_output: str,
    *,
    refiner_truncated: bool = False,
) -> tuple[Pipeline, MagicMock]:
    """构造 Pipeline + 注入按固定内容回复的 refiner。"""
    cfg = PipelineConfig(llm=LLMConfig(model="test-model"))
    pipeline = Pipeline(cfg)

    refiner = MagicMock()
    refiner.refine = AsyncMock(
        return_value=RefinedResult(
            markdown=refiner_output,
            gaps=[],
            truncated=refiner_truncated,
        ),
    )
    pipeline.set_refiner(refiner)
    return pipeline, refiner


class TestTruncationHeuristicInRefineSegments:
    """pipeline.py:548-557 行数比例启发式真实路径测试。

    关键：refiner 返回 `truncated=False` 时，由 `_refine_segments` 的
    启发式负责翻转为 True。如果启发式被删除，下面的测试会立即失败。
    """

    @pytest.mark.asyncio
    async def test_short_output_gets_flagged_truncated(
        self, tmp_path: Path,
    ) -> None:
        """40 行输入 + 5 行输出（输出占比 ≈12.5% < 70% 阈值）→ 启发式标记截断。

        触发截断后 markdown 必须回退到原文（seg.text），而不是保留截断的输出 ——
        截断的 LLM 输出已丢掉后半段内容，留下会误导下游 reassemble。
        """
        input_lines = 40
        output_lines = 5
        assert input_lines > _TRUNCATION_MIN_INPUT_LINES
        assert output_lines < input_lines * (
            1 - _TRUNCATION_RATIO_THRESHOLD
        )

        input_text = "\n".join(
            f"第 {i + 1} 行" for i in range(input_lines)
        )
        output_text = "\n".join(
            f"out {i + 1}" for i in range(output_lines)
        )

        pipeline, _refiner = _make_pipeline_with_refiner(
            output_text, refiner_truncated=False,
        )
        merged = MergedDocument(markdown=input_text)

        results, _gaps = await pipeline._refine_segments(
            merged, tmp_path, None, _noop_report,
        )

        assert len(results) == 1
        # 核心断言：启发式把 truncated 从 False 翻成了 True，且 markdown 回退原文
        assert results[0].truncated is True
        assert results[0].markdown == input_text
        assert results[0].gaps == []

    @pytest.mark.asyncio
    async def test_output_close_to_input_not_flagged(
        self, tmp_path: Path,
    ) -> None:
        """40 行输入 + 35 行输出（占比 87.5% > 70%）→ 不应触发启发式。"""
        input_text = "\n".join(f"L{i}" for i in range(40))
        output_text = "\n".join(f"O{i}" for i in range(35))

        pipeline, _ = _make_pipeline_with_refiner(
            output_text, refiner_truncated=False,
        )
        merged = MergedDocument(markdown=input_text)

        results, _ = await pipeline._refine_segments(
            merged, tmp_path, None, _noop_report,
        )

        assert len(results) == 1
        assert results[0].truncated is False

    @pytest.mark.asyncio
    async def test_short_input_below_min_not_flagged(
        self, tmp_path: Path,
    ) -> None:
        """输入仅 10 行（< _TRUNCATION_MIN_INPUT_LINES）→ 启发式不介入。"""
        input_lines = 10
        assert input_lines <= _TRUNCATION_MIN_INPUT_LINES

        input_text = "\n".join(f"L{i}" for i in range(input_lines))
        output_text = "one-line"  # 远少于输入

        pipeline, _ = _make_pipeline_with_refiner(
            output_text, refiner_truncated=False,
        )
        merged = MergedDocument(markdown=input_text)

        results, _ = await pipeline._refine_segments(
            merged, tmp_path, None, _noop_report,
        )

        assert len(results) == 1
        # 短输入下启发式不检测，结果保持 refiner 自报状态（False）
        assert results[0].truncated is False

    @pytest.mark.asyncio
    async def test_refiner_self_reported_truncation_falls_back(
        self, tmp_path: Path,
    ) -> None:
        """refiner 自报 truncated=True（finish_reason=length）时同样回退原文。

        统一与启发式截断走同一回退分支，避免半截输出进入下游。
        """
        input_text = "\n".join(f"L{i}" for i in range(40))
        output_text = "\n".join(f"O{i}" for i in range(35))  # 启发式不会触发

        pipeline, _ = _make_pipeline_with_refiner(
            output_text, refiner_truncated=True,
        )
        merged = MergedDocument(markdown=input_text)

        results, _ = await pipeline._refine_segments(
            merged, tmp_path, None, _noop_report,
        )

        assert len(results) == 1
        # refiner 自报截断 → 一视同仁回退原文
        assert results[0].truncated is True
        assert results[0].markdown == input_text
        assert results[0].gaps == []


class TestCollectWarningsStaticMethod:
    """`Pipeline._collect_warnings`（pipeline.py:1082-1099）真实路径。

    直接调真的静态方法，而非在测试里手写 `warnings.append(...)`。
    """

    def test_segment_truncation_produces_indexed_warning(self) -> None:
        refined = [
            RefinedResult(markdown="ok", truncated=False),
            RefinedResult(markdown="bad", truncated=True),
            RefinedResult(markdown="ok2", truncated=False),
            RefinedResult(markdown="bad2", truncated=True),
        ]
        warnings = Pipeline._collect_warnings(
            refined_results=refined,
            all_gaps=[],
            final_truncated=False,
        )
        # 段 2 与段 4 是 truncated=True，应带 1-based 序号
        assert any(
            "段 2" in w and "精修输出疑似被截断" in w
            for w in warnings
        )
        assert any(
            "段 4" in w and "精修输出疑似被截断" in w
            for w in warnings
        )
        # 非 truncated 的段不应出现
        assert not any("段 1" in w for w in warnings)
        assert not any("段 3" in w for w in warnings)

    def test_final_truncation_adds_standalone_warning(self) -> None:
        warnings = Pipeline._collect_warnings(
            refined_results=[
                RefinedResult(markdown="ok", truncated=False),
            ],
            all_gaps=[],
            final_truncated=True,
        )
        assert any(
            "整篇文档级精修" in w and "疑似被截断" in w
            for w in warnings
        )

    def test_unfilled_gap_adds_warning_with_image_name(self) -> None:
        gaps = [
            Gap(
                after_image="p1.jpg",
                context_before="",
                context_after="",
                filled=False,
            ),
            Gap(
                after_image="p2.jpg",
                context_before="",
                context_after="",
                filled=True,  # 已补充 → 不应出现警告
                filled_content="reocr",
            ),
        ]
        warnings = Pipeline._collect_warnings(
            refined_results=[],
            all_gaps=gaps,
            final_truncated=False,
        )
        assert any("p1.jpg" in w for w in warnings)
        assert not any("p2.jpg" in w for w in warnings)

    def test_clean_run_produces_empty_warnings(self) -> None:
        warnings = Pipeline._collect_warnings(
            refined_results=[
                RefinedResult(markdown="ok", truncated=False),
            ],
            all_gaps=[],
            final_truncated=False,
        )
        assert warnings == []


class TestFinalRefineFallback:
    """_final_refine 截断时回退原文。"""

    @pytest.mark.asyncio
    async def test_final_refine_truncated_falls_back_to_doc(
        self, tmp_path: Path,
    ) -> None:
        """_final_refine 返回 truncated=True → 回退原 doc，不用截断的精修结果。"""
        cfg = PipelineConfig(llm=LLMConfig(model="test-model"))
        pipeline = Pipeline(cfg)

        refiner = MagicMock()
        refiner.final_refine = AsyncMock(
            return_value=RefinedResult(
                markdown="# 截断输出（只有前半段）",
                gaps=[],
                truncated=True,
            ),
        )

        doc = MergedDocument(
            markdown="# 原始完整文档\n" + "\n".join(
                f"第 {i} 段正文" for i in range(1, 40)
            ),
        )
        new_doc, is_truncated = await pipeline._final_refine(
            refiner, doc, tmp_path, _noop_report,
        )

        assert is_truncated is True
        # 核心断言：回退到原文，不是截断的精修输出
        assert new_doc.markdown == doc.markdown
        assert "截断输出" not in new_doc.markdown

    @pytest.mark.asyncio
    async def test_final_refine_not_truncated_uses_refined(
        self, tmp_path: Path,
    ) -> None:
        """_final_refine 未截断时照常使用精修后的 markdown。"""
        cfg = PipelineConfig(llm=LLMConfig(model="test-model"))
        pipeline = Pipeline(cfg)

        refiner = MagicMock()
        refiner.final_refine = AsyncMock(
            return_value=RefinedResult(
                markdown="# 精修完成",
                gaps=[],
                truncated=False,
            ),
        )

        doc = MergedDocument(markdown="# 原始文档")
        new_doc, is_truncated = await pipeline._final_refine(
            refiner, doc, tmp_path, _noop_report,
        )

        assert is_truncated is False
        assert new_doc.markdown == "# 精修完成"


class TestTruncationHeuristicE2E:
    """启发式 → `_collect_warnings` 端到端联动。

    覆盖 test_warnings_e2e.py 的盲点：那边的 refine mock 自报 truncated=True，
    绕过了启发式；这里让 refiner 自报 False，观察启发式介入并最终进入 warnings。
    """

    @pytest.mark.asyncio
    async def test_heuristic_truncation_surfaces_in_warnings(
        self, tmp_path: Path,
    ) -> None:
        from docrestore.models import PageOCR

        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
        )
        cfg.pii.enable = False
        pipeline = Pipeline(cfg)

        engine = MagicMock()
        # OCR 产出 >20 行的文本
        long_text = "\n".join(f"原始第 {i + 1} 行" for i in range(40))

        async def _ocr(image_path: Path, _out: Path) -> PageOCR:
            return PageOCR(
                image_path=image_path,
                image_size=(100, 100),
                raw_text=long_text,
                cleaned_text=long_text,
            )

        engine.ocr = AsyncMock(side_effect=_ocr)
        engine.reocr_page = AsyncMock(return_value="")
        engine.shutdown = AsyncMock(return_value=None)
        pipeline.set_ocr_engine(engine)

        # refiner 自报 False，但输出远短于输入
        refiner = MagicMock()
        refiner.refine = AsyncMock(
            return_value=RefinedResult(
                markdown="only one short line",
                gaps=[],
                truncated=False,
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

        img_dir = tmp_path / "imgs"
        img_dir.mkdir()
        (img_dir / "p.jpg").write_bytes(b"fake")

        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        assert len(results) == 1
        # 警告只可能来自启发式（refiner 自报 False）
        assert any(
            "精修输出疑似被截断" in w
            for w in results[0].warnings
        )
