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

"""Pipeline 全链路端到端测试（纯 mock，CI 友好）

使用 AsyncMock 替换 OCR 引擎和 LLM refiner，不依赖 GPU / API key / 真实图片。
覆盖全流程：OCR → clean → dedup → PII → refine → reassemble → gap fill →
 final refine → render。
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


def _make_mock_refiner(
    *,
    boundaries: list[DocBoundary] | None = None,
    gaps: list[Gap] | None = None,
    fill_result: str = "",
) -> MagicMock:
    """构造完整能力的 mock refiner。"""
    refiner = MagicMock()

    async def _refine(text: str, _ctx: object) -> object:
        return MagicMock(
            markdown=text,
            gaps=gaps or [],
            truncated=False,
        )

    refiner.refine = AsyncMock(side_effect=_refine)
    refiner.final_refine = AsyncMock(
        side_effect=lambda md: MagicMock(
            markdown=md, gaps=[], truncated=False,
        ),
    )
    refiner.detect_doc_boundaries = AsyncMock(
        return_value=boundaries or [],
    )
    refiner.detect_pii_entities = AsyncMock(
        return_value=([], []),
    )
    refiner.fill_gap = AsyncMock(return_value=fill_result)
    return refiner


def _make_mock_ocr_engine(
    page_texts: dict[str, str],
) -> MagicMock:
    """按文件名→文本的映射构造 mock OCR 引擎。"""
    engine = MagicMock()

    async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
        text = page_texts.get(image_path.name, f"{image_path.name} 正文")
        return PageOCR(
            image_path=image_path,
            image_size=(100, 100),
            raw_text=text,
            cleaned_text=text,
        )

    engine.ocr = AsyncMock(side_effect=_ocr)
    engine.shutdown = AsyncMock(return_value=None)
    engine.reocr_page = AsyncMock(return_value="")
    return engine


def _build_image_dir(
    tmp_path: Path, file_names: list[str],
) -> Path:
    """在 tmp_path/imgs 下创建假图片文件。"""
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    for name in file_names:
        (img_dir / name).write_bytes(b"fake")
    return img_dir


class TestSingleDocFullChain:
    """单文档 → 单次 OCR → 合并 → 精修 → 渲染"""

    @pytest.mark.asyncio
    async def test_single_doc_renders_markdown(
        self, tmp_path: Path,
    ) -> None:
        """两页输入 → 产出 document.md，markdown 包含两页内容。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        texts = {
            "page1.jpg": "# 章节一\n第一段内容",
            "page2.jpg": "# 章节二\n第二段内容",
        }
        pipeline.set_ocr_engine(_make_mock_ocr_engine(texts))
        pipeline.set_refiner(_make_mock_refiner())

        img_dir = _build_image_dir(
            tmp_path, ["page1.jpg", "page2.jpg"],
        )
        out_dir = tmp_path / "out"

        results = await pipeline.process_many(img_dir, out_dir)

        assert len(results) == 1
        result = results[0]
        assert result.output_path.exists()
        assert result.output_path.name == "document.md"
        # 两页内容都应出现在最终 markdown
        assert "第一段内容" in result.markdown
        assert "第二段内容" in result.markdown
        # 单文档 doc_dir 为空
        assert result.doc_dir == ""

    @pytest.mark.asyncio
    async def test_refiner_receives_cleaned_and_merged_text(
        self, tmp_path: Path,
    ) -> None:
        """refine 收到的应是合并后的 markdown（含 page marker）。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        pipeline.set_ocr_engine(
            _make_mock_ocr_engine(
                {"a.jpg": "a正文", "b.jpg": "b正文"},
            ),
        )

        received: list[str] = []

        async def _refine(text: str, _ctx: object) -> object:
            received.append(text)
            return MagicMock(
                markdown=text, gaps=[], truncated=False,
            )

        refiner = _make_mock_refiner()
        refiner.refine = AsyncMock(side_effect=_refine)
        pipeline.set_refiner(refiner)

        img_dir = _build_image_dir(tmp_path, ["a.jpg", "b.jpg"])
        await pipeline.process_many(img_dir, tmp_path / "out")

        assert received, "refine 至少被调用一次"
        # 合并阶段插入了 page marker
        combined = "\n".join(received)
        assert "page: a.jpg" in combined
        assert "page: b.jpg" in combined


class TestMultiDocFullChain:
    """文档边界检测 → 拆分成多个子文档分别渲染"""

    @pytest.mark.asyncio
    async def test_two_docs_split_into_subdirs(
        self, tmp_path: Path,
    ) -> None:
        """检测到 1 个边界 → 2 个 PipelineResult，各自一个子目录。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        pipeline.set_ocr_engine(
            _make_mock_ocr_engine({
                "p1.jpg": "# 报告A\n内容A",
                "p2.jpg": "# 报告B\n内容B",
                "p3.jpg": "内容B续",
            }),
        )
        pipeline.set_refiner(
            _make_mock_refiner(
                boundaries=[
                    DocBoundary(after_page="p1.jpg", new_title="报告B"),
                ],
            ),
        )

        img_dir = _build_image_dir(
            tmp_path, ["p1.jpg", "p2.jpg", "p3.jpg"],
        )
        out_dir = tmp_path / "out"

        results = await pipeline.process_many(img_dir, out_dir)

        assert len(results) == 2
        # 每篇子文档应有独立的子目录输出
        assert all(r.doc_dir != "" for r in results)
        assert all(r.output_path.exists() for r in results)
        # 输出路径互不相同
        paths = {r.output_path for r in results}
        assert len(paths) == 2


class TestGapFillFullChain:
    """含 gap 的全链路：refine 标记 gap → reOCR + LLM 补充"""

    @pytest.mark.asyncio
    async def test_gap_fill_invokes_reocr_and_fill(
        self, tmp_path: Path,
    ) -> None:
        """refine 返回 gap → pipeline 调用 reocr_page 和 fill_gap。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=True,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        engine = _make_mock_ocr_engine({
            "a.jpg": "# 标题\n前半",
            "b.jpg": "后半",
        })
        pipeline.set_ocr_engine(engine)

        # refine 第一段返回一个 gap（after_image=a.jpg）
        gap = Gap(
            after_image="a.jpg",
            context_before="前半",
            context_after="后半",
        )
        refiner = _make_mock_refiner(
            gaps=[gap], fill_result="被补充的内容",
        )
        pipeline.set_refiner(refiner)

        img_dir = _build_image_dir(tmp_path, ["a.jpg", "b.jpg"])
        out_dir = tmp_path / "out"

        results = await pipeline.process_many(img_dir, out_dir)

        assert len(results) == 1
        result = results[0]
        # reocr_page 被调用（gap fill 需要重新 OCR）
        engine.reocr_page.assert_awaited()
        # fill_gap 至少被调用一次
        refiner.fill_gap.assert_awaited()
        # 最终 markdown 包含补充内容
        assert "被补充的内容" in result.markdown


class TestPIIFullChain:
    """PII 启用下完整链路：regex 脱敏 + refine 收到脱敏文本"""

    @pytest.mark.asyncio
    async def test_pii_redacted_in_final_output(
        self, tmp_path: Path,
    ) -> None:
        """手机号在最终 markdown 中被替换为占位符。"""
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(
                enable=True,
                block_cloud_on_detect_failure=False,
            ),
        )
        pipeline = Pipeline(cfg)

        pipeline.set_ocr_engine(
            _make_mock_ocr_engine({
                "p.jpg": "联系 13812345678 或邮箱 abc@example.com",
            }),
        )
        pipeline.set_refiner(_make_mock_refiner())

        img_dir = _build_image_dir(tmp_path, ["p.jpg"])
        out_dir = tmp_path / "out"

        results = await pipeline.process_many(img_dir, out_dir)
        result = results[0]

        # 原始敏感信息应被替换
        assert "13812345678" not in result.markdown
        assert "abc@example.com" not in result.markdown
        # 脱敏记录被落账
        assert len(result.redaction_records) > 0


class TestNoImageRaises:
    """空目录抛 FileNotFoundError"""

    @pytest.mark.asyncio
    async def test_empty_dir_raises(self, tmp_path: Path) -> None:
        cfg = PipelineConfig(
            llm=LLMConfig(model="test"),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(_make_mock_ocr_engine({}))
        pipeline.set_refiner(_make_mock_refiner())

        empty = tmp_path / "empty"
        empty.mkdir()

        with pytest.raises(FileNotFoundError):
            await pipeline.process_many(empty, tmp_path / "out")
