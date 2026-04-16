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

"""LocalLLMRefiner 单元测试（mock litellm）"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from docrestore.llm.base import BaseLLMRefiner
from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.local import LocalLLMRefiner
from docrestore.models import Gap, RefineContext
from docrestore.pipeline.config import LLMConfig


def _make_config() -> LLMConfig:
    """构造测试用 LLMConfig。"""
    return LLMConfig(
        provider="local",
        model="ollama/qwen2.5",
        api_base="http://localhost:11434/v1",
    )


def _mock_response(content: str, finish_reason: str = "stop") -> object:
    """构造 litellm 响应对象。"""
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content),
        finish_reason=finish_reason,
    )
    return SimpleNamespace(choices=[choice])


class TestLocalLLMRefinerInstantiation:
    """实例化与继承检查"""

    def test_is_base_subclass(self) -> None:
        """LocalLLMRefiner 继承 BaseLLMRefiner"""
        refiner = LocalLLMRefiner(_make_config())
        assert isinstance(refiner, BaseLLMRefiner)

    @pytest.mark.asyncio
    async def test_detect_pii_entities_returns_empty(self) -> None:
        """LocalLLMRefiner.detect_pii_entities 返回空列表（不检测）"""
        refiner = LocalLLMRefiner(_make_config())
        person, org = await refiner.detect_pii_entities("张三来自 ACME")
        assert person == []
        assert org == []

    def test_not_cloud_instance(self) -> None:
        """LocalLLMRefiner 不是 CloudLLMRefiner 实例"""
        refiner = LocalLLMRefiner(_make_config())
        assert not isinstance(refiner, CloudLLMRefiner)


class TestLocalLLMRefinerRefine:
    """refine() 方法测试"""

    @pytest.mark.asyncio
    async def test_refine_returns_refined_result(self) -> None:
        """refine() 返回 RefinedResult 且包含 LLM 输出内容"""
        refiner = LocalLLMRefiner(_make_config())
        ctx = RefineContext(
            segment_index=1,
            total_segments=1,
            overlap_before="",
            overlap_after="",
        )

        mock_resp = _mock_response("# 精修后的内容\n\n这是测试文本。")
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await refiner.refine("# 原始内容", ctx)

        assert "精修后的内容" in result.markdown
        assert isinstance(result.gaps, list)
        assert result.truncated is False


class TestLocalLLMRefinerFillGap:
    """fill_gap() 方法测试"""

    @pytest.mark.asyncio
    async def test_fill_gap_returns_content(self) -> None:
        """fill_gap() 正常返回填充内容"""
        refiner = LocalLLMRefiner(_make_config())
        gap = Gap(
            after_image="page1.jpg",
            context_before="上文末尾",
            context_after="下文开头",
        )

        mock_resp = _mock_response("补充的缺失内容段落。")
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            filled = await refiner.fill_gap(
                gap, "当前页 OCR 文本",
            )

        assert filled == "补充的缺失内容段落。"

    @pytest.mark.asyncio
    async def test_fill_gap_empty_marker(self) -> None:
        """fill_gap() 收到空标记时返回空字符串"""
        refiner = LocalLLMRefiner(_make_config())
        gap = Gap(
            after_image="page1.jpg",
            context_before="上文",
            context_after="下文",
        )

        mock_resp = _mock_response("[无法补充]")
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            filled = await refiner.fill_gap(
                gap, "当前页文本",
            )

        assert filled == ""


class TestLocalLLMRefinerFinalRefine:
    """final_refine() 方法测试"""

    @pytest.mark.asyncio
    async def test_final_refine_returns_result(self) -> None:
        """final_refine() 返回 RefinedResult"""
        refiner = LocalLLMRefiner(_make_config())

        mock_resp = _mock_response("整篇精修后的文档内容。")
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await refiner.final_refine("原始全文")

        assert "整篇精修后的文档内容" in result.markdown
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_final_refine_detects_truncation(self) -> None:
        """final_refine() 检测 finish_reason=length 截断"""
        refiner = LocalLLMRefiner(_make_config())

        mock_resp = _mock_response("被截断的内容", "length")
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await refiner.final_refine("原始全文")

        assert result.truncated is True
