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

"""CloudLLMRefiner 截断检测 + PII JSON 解析测试（mock litellm）"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from docrestore.llm.cloud import (
    CloudLLMRefiner,
    _coerce_str_list,
    _extract_json_payload,
)
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


class TestExtractJsonPayload:
    """_extract_json_payload 剥 markdown code fence。"""

    def test_plain_json_unchanged(self) -> None:
        raw = '{"a": 1}'
        assert _extract_json_payload(raw) == '{"a": 1}'

    def test_json_code_fence_stripped(self) -> None:
        """```json\\n...\\n``` 剥围栏。"""
        raw = '```json\n{"a": 1}\n```'
        assert _extract_json_payload(raw) == '{"a": 1}'

    def test_plain_code_fence_stripped(self) -> None:
        """```\\n...\\n``` （无语言标签）剥围栏。"""
        raw = '```\n{"a": 1}\n```'
        assert _extract_json_payload(raw) == '{"a": 1}'

    def test_code_fence_with_surrounding_whitespace(self) -> None:
        raw = '  \n```json\n{"a": 1, "b": 2}\n```  \n'
        assert _extract_json_payload(raw) == '{"a": 1, "b": 2}'

    def test_explanatory_text_around_json(self) -> None:
        """前后带解释说明 → 取首个 { 到末 }。"""
        raw = '以下是检测结果：\n{"a": 1}\n希望对你有帮助。'
        assert _extract_json_payload(raw) == '{"a": 1}'


class TestDetectPiiEntitiesJsonParse:
    """detect_pii_entities 能处理 markdown code fence 响应。"""

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_code_fenced_json_parsed(
        self, mock_acompletion: AsyncMock,
    ) -> None:
        """LLM 用 ```json ... ``` 包裹响应时也能解析（复现用户场景）。"""
        fenced = (
            '```json\n'
            '{"person_names": ["Charles Cazabon"], '
            '"org_names": ["\u5e73\u5934\u54e5"]}\n'
            '```'
        )
        mock_acompletion.return_value = _make_response(fenced, "stop")

        refiner = CloudLLMRefiner(_make_config())
        persons, orgs = await refiner.detect_pii_entities("test")

        assert persons == ["Charles Cazabon"]
        assert orgs == ["\u5e73\u5934\u54e5"]

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_plain_json_still_parsed(
        self, mock_acompletion: AsyncMock,
    ) -> None:
        """裸 JSON 响应也能正常解析。"""
        raw = '{"person_names": ["\u674e\u56db"], "org_names": []}'
        mock_acompletion.return_value = _make_response(raw, "stop")

        refiner = CloudLLMRefiner(_make_config())
        persons, orgs = await refiner.detect_pii_entities("test")

        assert persons == ["\u674e\u56db"]
        assert orgs == []

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_non_json_raises_runtime(
        self, mock_acompletion: AsyncMock,
    ) -> None:
        """完全不是 JSON（连 {} 都没有）→ RuntimeError。"""
        mock_acompletion.return_value = _make_response(
            "抱歉，我无法理解你的问题。", "stop",
        )
        refiner = CloudLLMRefiner(_make_config())
        with pytest.raises(RuntimeError, match="非 JSON"):
            await refiner.detect_pii_entities("test")

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_bare_string_field_not_char_split(
        self, mock_acompletion: AsyncMock,
    ) -> None:
        """#13：person_names 是裸字符串而非数组 → 丢弃，绝不逐字符拆分。

        旧实现 list("Alice") = ['A','l','i','c','e']，下游全局替换会把
        正文每个字母打碎。修复后裸字符串视为字段缺失，返回空。
        """
        raw = '{"person_names": "Alice", "org_names": ["Acme"]}'
        mock_acompletion.return_value = _make_response(raw, "stop")

        refiner = CloudLLMRefiner(_make_config())
        persons, orgs = await refiner.detect_pii_entities("test")

        assert persons == []  # 不是 ['A','l','i','c','e']
        assert orgs == ["Acme"]

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_mixed_type_items_filtered(
        self, mock_acompletion: AsyncMock,
    ) -> None:
        """#13：数组内混入 None/数字/空串 → 仅保留去空格后非空的 str。"""
        raw = (
            '{"person_names": ["张三", null, 42, "  ", " 李四 "],'
            ' "org_names": []}'
        )
        mock_acompletion.return_value = _make_response(raw, "stop")

        refiner = CloudLLMRefiner(_make_config())
        persons, orgs = await refiner.detect_pii_entities("test")

        assert persons == ["张三", "李四"]
        assert orgs == []

    @pytest.mark.asyncio
    @patch("docrestore.llm.base.litellm.acompletion")
    async def test_top_level_array_raises(
        self, mock_acompletion: AsyncMock,
    ) -> None:
        """#13：顶层是 JSON 数组而非对象 → RuntimeError（fail-closed）。"""
        mock_acompletion.return_value = _make_response(
            '["张三", "李四"]', "stop",
        )
        refiner = CloudLLMRefiner(_make_config())
        with pytest.raises(RuntimeError, match="非对象"):
            await refiner.detect_pii_entities("test")


class TestCoerceStrList:
    """_coerce_str_list 类型守卫（issue #13）。"""

    def test_bare_string_returns_empty(self) -> None:
        """裸字符串非 list → 空（绝不逐字符拆分）。"""
        assert _coerce_str_list("Alice") == []

    def test_non_list_returns_empty(self) -> None:
        assert _coerce_str_list(None) == []
        assert _coerce_str_list(42) == []
        assert _coerce_str_list({"a": 1}) == []

    def test_filters_non_str_and_blank(self) -> None:
        value: object = ["a", None, 1, "", "  ", " b "]
        assert _coerce_str_list(value) == ["a", "b"]

    def test_plain_str_list_passthrough(self) -> None:
        assert _coerce_str_list(["x", "y"]) == ["x", "y"]
