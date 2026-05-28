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
# 本文件测试已 skip（流式版停用 DOC_BOUNDARY 聚合），类型签名与新接口不匹配
"""DocBoundary + GapFill 组合端到端测试（纯 mock，CI 友好）

验证同时开启文档边界检测与缺口补充时：
- 两篇子文档各自落到独立目录
- gap 按 after_image 被正确分派到所属子文档，只补充到该子文档
- 另一篇子文档不会出现属于他人的补充内容
- reocr_page / fill_gap 只为有 gap 的页面调用
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.models import DocBoundary, Gap, PageOCR
from docrestore.pipeline.config import (
    LLMConfig,
    PIIConfig,
    PipelineConfig,
)
from docrestore.pipeline.pipeline import Pipeline


def _make_engine(page_texts: dict[str, str]) -> MagicMock:
    """OCR 引擎 mock：按文件名返回指定文本，reocr_page 按文件名返回补充源。"""
    engine = MagicMock()

    async def _ocr(image_path: Path, _out: Path) -> PageOCR:
        text = page_texts.get(image_path.name, image_path.name)
        return PageOCR(
            image_path=image_path,
            image_size=(100, 100),
            raw_text=text,
            cleaned_text=text,
        )

    async def _reocr(image_path: Path) -> str:
        return f"reocr:{image_path.name}"

    engine.ocr = AsyncMock(side_effect=_ocr)
    engine.reocr_page = AsyncMock(side_effect=_reocr)
    engine.shutdown = AsyncMock(return_value=None)
    return engine


def _make_refiner(
    *,
    boundaries: list[DocBoundary],
    per_page_gap: dict[str, str],
) -> MagicMock:
    """refine 按输入文本里出现的 page marker 返回对应 gap。"""
    refiner = MagicMock()

    async def _refine(text: str, _ctx: object) -> object:
        found: list[Gap] = []
        for page_name, ctx in per_page_gap.items():
            if f"page: {page_name}" in text:
                found.append(
                    Gap(
                        after_image=page_name,
                        context_before=ctx,
                        context_after="",
                    ),
                )
        return MagicMock(markdown=text, gaps=found, truncated=False)

    async def _fill_gap(
        gap: Gap,
        _current: str,
        _next_text: str | None,
        _next_name: str | None,
    ) -> str:
        return f"filled-{gap.after_image}"

    refiner.refine = AsyncMock(side_effect=_refine)
    refiner.final_refine = AsyncMock(
        side_effect=lambda md: MagicMock(
            markdown=md, gaps=[], truncated=False,
        ),
    )
    refiner.detect_doc_boundaries = AsyncMock(return_value=boundaries)
    refiner.detect_pii_entities = AsyncMock(return_value=([], []))
    refiner.fill_gap = AsyncMock(side_effect=_fill_gap)
    return refiner


def _build_image_dir(root: Path, names: list[str]) -> Path:
    img_dir = root / "imgs"
    img_dir.mkdir()
    for n in names:
        (img_dir / n).write_bytes(b"fake")
    return img_dir


@pytest.mark.skip(
    reason="流式 Pipeline 停用 DOC_BOUNDARY 聚合（streaming-pipeline §10）；"
    "下一版代码照片还原恢复聚合时再启用",
)
class TestBoundaryAndGapFillTogether:
    """同一次 process_many 同时触发 DocBoundary 拆分 + GapFill 补充"""

    @pytest.mark.asyncio
    async def test_gap_filled_only_in_owning_subdoc(
        self, tmp_path: Path,
    ) -> None:
        """p1 的 gap 只进 doc1；p3 的 gap 只进 doc2，互不串。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=True,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        pipeline.set_ocr_engine(_make_engine({
            "p1.jpg": "# 报告A\nA 前半",
            "p2.jpg": "A 后半",
            "p3.jpg": "# 报告B\nB 前半",
            "p4.jpg": "B 后半",
        }))
        # p2 后切分为两篇文档
        pipeline.set_refiner(_make_refiner(
            boundaries=[
                DocBoundary(after_page="p2.jpg", new_title="报告B"),
            ],
            # p1 有缺口（属于第一篇），p3 有缺口（属于第二篇）
            per_page_gap={
                "p1.jpg": "A 前半",
                "p3.jpg": "B 前半",
            },
        ))

        img_dir = _build_image_dir(
            tmp_path, ["p1.jpg", "p2.jpg", "p3.jpg", "p4.jpg"],
        )
        out_dir = tmp_path / "out"

        results = await pipeline.process_many(img_dir, out_dir)

        assert len(results) == 2
        # 每篇都落盘 + 有独立 doc_dir
        assert all(r.output_path.exists() for r in results)
        assert all(r.doc_dir != "" for r in results)
        assert len({r.output_path for r in results}) == 2

        # 按 doc_dir 命中具体子文档
        by_dir = {r.doc_dir: r for r in results}
        first = next(r for r in results if "p1.jpg" in r.markdown)
        second = next(r for r in results if "p3.jpg" in r.markdown)

        # 各自补充内容只出现在自己的 markdown
        assert "filled-p1.jpg" in first.markdown
        assert "filled-p3.jpg" not in first.markdown
        assert "filled-p3.jpg" in second.markdown
        assert "filled-p1.jpg" not in second.markdown
        # 至少两个不同的子目录
        assert len(by_dir) == 2

    @pytest.mark.asyncio
    async def test_boundary_without_gaps_still_splits(
        self, tmp_path: Path,
    ) -> None:
        """仅边界无 gap → 拆分正常，fill_gap 不被调用。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=True,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        engine = _make_engine({
            "p1.jpg": "# A\n内容A",
            "p2.jpg": "# B\n内容B",
        })
        refiner = _make_refiner(
            boundaries=[
                DocBoundary(after_page="p1.jpg", new_title="B"),
            ],
            per_page_gap={},
        )
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        img_dir = _build_image_dir(tmp_path, ["p1.jpg", "p2.jpg"])
        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        assert len(results) == 2
        refiner.fill_gap.assert_not_awaited()
        engine.reocr_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gap_in_last_subdoc_only(
        self, tmp_path: Path,
    ) -> None:
        """只有最后一篇存在 gap → 只那篇触发 reocr+fill，其余不动。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=True,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        engine = _make_engine({
            "p1.jpg": "# A\n内容A",
            "p2.jpg": "# B\n内容B",
            "p3.jpg": "更多 B",
        })
        # 仅 p3 有 gap（在第二篇内）
        refiner = _make_refiner(
            boundaries=[
                DocBoundary(after_page="p1.jpg", new_title="B"),
            ],
            per_page_gap={"p3.jpg": "更多 B"},
        )
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        img_dir = _build_image_dir(
            tmp_path, ["p1.jpg", "p2.jpg", "p3.jpg"],
        )
        results = await pipeline.process_many(
            img_dir, tmp_path / "out",
        )

        assert len(results) == 2
        # 有 gap 的那一篇含 filled
        doc_with_fill = next(
            r for r in results if "filled-p3.jpg" in r.markdown
        )
        assert doc_with_fill is not None
        # 另一篇不应有任何 "filled-" 串
        others = [r for r in results if r is not doc_with_fill]
        assert all("filled-" not in r.markdown for r in others)
        # fill_gap 恰好被调用 1 次（只有 p3 这个 gap）
        assert refiner.fill_gap.await_count == 1
