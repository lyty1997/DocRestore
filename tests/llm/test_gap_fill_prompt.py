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

"""Gap fill prompt 构造 + CloudLLMRefiner.fill_gap() 测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.prompts import (
    GAP_FILL_EMPTY_MARKER,
    GAP_FILL_SYSTEM_PROMPT,
    build_gap_fill_prompt,
)
from docrestore.models import Gap
from docrestore.pipeline.config import LLMConfig


def _make_gap(
    after_image: str = "page1.jpg",
    context_before: str = "前文内容",
    context_after: str = "后文内容",
) -> Gap:
    """构造测试用 Gap 对象。"""
    return Gap(
        after_image=after_image,
        context_before=context_before,
        context_after=context_after,
    )


class TestBuildGapFillPrompt:
    """build_gap_fill_prompt() 结构测试"""

    def test_basic_structure(self) -> None:
        """生成的 messages 包含 system 和 user 两条。"""
        gap = _make_gap()
        messages = build_gap_fill_prompt(gap, "OCR 当前页文本")

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == GAP_FILL_SYSTEM_PROMPT
        assert messages[1]["role"] == "user"

    def test_user_content_contains_gap_info(self) -> None:
        """user 消息包含 gap 的关键信息。"""
        gap = _make_gap(
            after_image="page01.jpg",
            context_before="章节一末尾",
            context_after="章节二开头",
        )
        messages = build_gap_fill_prompt(gap, "当前页OCR")
        user_content = messages[1]["content"]

        assert "page01.jpg" in user_content
        assert "章节一末尾" in user_content
        assert "章节二开头" in user_content
        assert "当前页OCR" in user_content

    def test_without_next_page(self) -> None:
        """不提供 next_page 时，user 消息不含下一页段落。"""
        gap = _make_gap()
        messages = build_gap_fill_prompt(gap, "当前页文本")
        user_content = messages[1]["content"]

        assert "下一页" not in user_content

    def test_with_next_page(self) -> None:
        """提供 next_page 时，user 消息包含下一页信息。"""
        gap = _make_gap()
        messages = build_gap_fill_prompt(
            gap,
            "当前页文本",
            next_page_text="下一页OCR内容",
            next_page_name="page2.jpg",
        )
        user_content = messages[1]["content"]

        assert "下一页（page2.jpg）" in user_content
        assert "下一页OCR内容" in user_content


class TestCloudLLMRefinerFillGap:
    """CloudLLMRefiner.fill_gap() 测试"""

    @pytest.mark.asyncio
    async def test_fill_gap_returns_content(self) -> None:
        """正常情况返回 LLM 提取的内容。"""
        config = LLMConfig(model="test-model")
        refiner = CloudLLMRefiner(config)
        gap = _make_gap()

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "这是缺失的段落内容"
        mock_response.choices = [mock_choice]

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await refiner.fill_gap(gap, "当前页OCR")

        assert result == "这是缺失的段落内容"

    @pytest.mark.asyncio
    async def test_fill_gap_empty_marker_returns_empty(self) -> None:
        """LLM 返回"无法补充"时返回空字符串。"""
        config = LLMConfig(model="test-model")
        refiner = CloudLLMRefiner(config)
        gap = _make_gap()

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = GAP_FILL_EMPTY_MARKER
        mock_response.choices = [mock_choice]

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await refiner.fill_gap(gap, "当前页OCR")

        assert result == ""

    @pytest.mark.asyncio
    async def test_fill_gap_empty_content_returns_empty(self) -> None:
        """LLM 返回空内容时返回空字符串。"""
        config = LLMConfig(model="test-model")
        refiner = CloudLLMRefiner(config)
        gap = _make_gap()

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_response.choices = [mock_choice]

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await refiner.fill_gap(gap, "当前页OCR")

        assert result == ""
