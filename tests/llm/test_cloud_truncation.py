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

"""CloudLLMRefiner 截断检测测试（mock litellm）"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.models import RefineContext
from docrestore.pipeline.config import LLMConfig


def _make_response(
    content: str, finish_reason: str
) -> SimpleNamespace:
    """构造 litellm 风格的 mock response。"""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(
        message=message, finish_reason=finish_reason
    )
    return SimpleNamespace(choices=[choice])


def _make_config() -> LLMConfig:
    """构造测试用 LLMConfig。"""
    return LLMConfig(
        model="test-model",
        api_base="https://example.com/v1",
        api_key="test-key",
    )


def _make_context() -> RefineContext:
    """构造测试用 RefineContext。"""
    return RefineContext(
        segment_index=1,
        total_segments=1,
        overlap_before="",
        overlap_after="",
    )


class TestCloudTruncationDetection:
    """finish_reason 截断检测测试"""

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_finish_reason_length_marks_truncated(
        self, mock_acompletion: AsyncMock
    ) -> None:
        """finish_reason='length' 时 truncated 应为 True。"""
        mock_acompletion.return_value = _make_response(
            "# 标题\n部分内容", "length"
        )
        refiner = CloudLLMRefiner(_make_config())
        result = await refiner.refine("# 原文", _make_context())

        assert result.truncated is True
        assert result.markdown == "# 标题\n部分内容"

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_finish_reason_stop_not_truncated(
        self, mock_acompletion: AsyncMock
    ) -> None:
        """finish_reason='stop' 时 truncated 应为 False。"""
        mock_acompletion.return_value = _make_response(
            "# 标题\n完整内容", "stop"
        )
        refiner = CloudLLMRefiner(_make_config())
        result = await refiner.refine("# 原文", _make_context())

        assert result.truncated is False
        assert result.markdown == "# 标题\n完整内容"

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_final_refine_truncation(
        self, mock_acompletion: AsyncMock
    ) -> None:
        """final_refine 同样检测 finish_reason='length'。"""
        mock_acompletion.return_value = _make_response(
            "# 精修后文档", "length"
        )
        refiner = CloudLLMRefiner(_make_config())
        result = await refiner.final_refine("# 原始文档")

        assert result.truncated is True

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_final_refine_no_truncation(
        self, mock_acompletion: AsyncMock
    ) -> None:
        """final_refine finish_reason='stop' 时不截断。"""
        mock_acompletion.return_value = _make_response(
            "# 精修后文档", "stop"
        )
        refiner = CloudLLMRefiner(_make_config())
        result = await refiner.final_refine("# 原始文档")

        assert result.truncated is False
