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

"""PIIRedactor 测试"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from docrestore.pipeline.config import CustomWord, PIIConfig
from docrestore.privacy.redactor import (
    EntityLexicon,
    PIIRedactor,
)


class TestRedactForCloudRegexOnly:
    """纯 regex 脱敏（无 LLM）"""

    @pytest.mark.asyncio
    async def test_regex_only_no_refiner(self) -> None:
        """无 refiner 时只做 regex 替换"""
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)
        text = "电话 13812345678 邮箱 a@b.com"
        result, records, lexicon = (
            await redactor.redact_for_cloud(text, None)
        )
        assert "13812345678" not in result
        assert "a@b.com" not in result
        assert cfg.phone_placeholder in result
        assert cfg.email_placeholder in result
        assert lexicon is None
        assert len(records) == 2


class TestRedactForCloudWithLLM:
    """regex + LLM mock 脱敏"""

    @pytest.mark.asyncio
    async def test_regex_plus_llm_entities(self) -> None:
        """人名/机构名也被替换"""
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)

        mock_refiner = AsyncMock()
        mock_refiner.detect_pii_entities = AsyncMock(
            return_value=(["张三"], ["腾讯公司"]),
        )

        text = "张三在腾讯公司工作，电话 13812345678"
        result, records, lexicon = (
            await redactor.redact_for_cloud(text, mock_refiner)
        )
        assert "张三" not in result
        assert "腾讯公司" not in result
        assert "13812345678" not in result
        assert cfg.person_name_placeholder in result
        assert cfg.org_name_placeholder in result
        assert lexicon is not None
        assert "张三" in lexicon.person_names

    @pytest.mark.asyncio
    async def test_llm_detect_failure_returns_none_lexicon(
        self,
    ) -> None:
        """LLM 检测失败时 lexicon 为 None"""
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)

        mock_refiner = AsyncMock()
        mock_refiner.detect_pii_entities = AsyncMock(
            side_effect=RuntimeError("API error"),
        )

        text = "张三在腾讯公司工作"
        result, records, lexicon = (
            await redactor.redact_for_cloud(text, mock_refiner)
        )
        # regex 无匹配，LLM 失败 → 原文不变
        assert lexicon is None

    @pytest.mark.asyncio
    async def test_entity_length_order(self) -> None:
        """实体按长度降序替换，防止短实体先匹配"""
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)

        mock_refiner = AsyncMock()
        mock_refiner.detect_pii_entities = AsyncMock(
            return_value=(["张三", "张三丰"], []),
        )

        text = "张三丰和张三都在场"
        result, records, lexicon = (
            await redactor.redact_for_cloud(text, mock_refiner)
        )
        assert "张三丰" not in result
        assert "张三" not in result
        assert cfg.person_name_placeholder in result


class TestRedactSnippet:
    """redact_snippet 轻量脱敏测试"""

    def test_snippet_with_lexicon(self) -> None:
        """复用 lexicon 替换 + regex"""
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)
        lexicon = EntityLexicon(
            person_names=("张三",),
            org_names=("腾讯公司",),
        )
        text = "张三电话 13812345678 在腾讯公司"
        result, records = redactor.redact_snippet(
            text, lexicon,
        )
        assert "张三" not in result
        assert "13812345678" not in result
        assert "腾讯公司" not in result
        kinds = [r.kind for r in records]
        assert "phone" in kinds
        assert "person_name" in kinds
        assert "org_name" in kinds

    def test_snippet_without_lexicon(self) -> None:
        """无 lexicon 时只做 regex"""
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)
        text = "张三电话 13812345678"
        result, records = redactor.redact_snippet(text, None)
        assert "13812345678" not in result
        # 张三不会被 regex 替换
        assert "张三" in result


class TestRedactRegexOnly:
    """redact_regex_only 为流式 Pipeline 提供的无 LLM 依赖入口"""

    def test_equivalent_to_snippet_without_lexicon(self) -> None:
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)
        text = "张三电话 13812345678，邮箱 a@b.com"
        result_regex, records_regex = redactor.redact_regex_only(text)
        result_snippet, records_snippet = redactor.redact_snippet(
            text, None,
        )
        assert result_regex == result_snippet
        assert [r.kind for r in records_regex] == [
            r.kind for r in records_snippet
        ]
        # 确认结构化 PII 被替换，人名保留（无 lexicon）
        assert "13812345678" not in result_regex
        assert "a@b.com" not in result_regex
        assert "张三" in result_regex

    def test_with_custom_words(self) -> None:
        cfg = PIIConfig(
            enable=True,
            custom_sensitive_words=[CustomWord(word="秘密项目")],
            custom_words_placeholder="[X]",
        )
        redactor = PIIRedactor(cfg)
        result, records = redactor.redact_regex_only(
            "这是秘密项目的文档",
        )
        assert "秘密项目" not in result
        assert "[X]" in result
        assert any(r.kind == "custom_word" for r in records)


class TestCustomSensitiveWords:
    """自定义敏感词 → 可选代号替换"""

    def test_custom_word_without_code_uses_placeholder(self) -> None:
        """未指定 code 的敏感词回退到默认占位符"""
        cfg = PIIConfig(
            enable=True,
            custom_sensitive_words=[CustomWord(word="秘密项目")],
            custom_words_placeholder="[X]",
        )
        redactor = PIIRedactor(cfg)
        result, records = redactor.redact_snippet(
            "这是秘密项目的文档", None,
        )
        assert "秘密项目" not in result
        assert "[X]" in result
        assert any(
            r.kind == "custom_word" and r.placeholder == "[X]"
            for r in records
        )

    def test_custom_word_with_code_uses_code(self) -> None:
        """指定 code 时用 code 替换"""
        cfg = PIIConfig(
            enable=True,
            custom_sensitive_words=[
                CustomWord(word="张伟", code="化名A"),
            ],
        )
        redactor = PIIRedactor(cfg)
        result, records = redactor.redact_snippet(
            "张伟说了什么", None,
        )
        assert "张伟" not in result
        assert "化名A" in result
        assert any(
            r.kind == "custom_word"
            and r.placeholder == "化名A"
            and r.count == 1
            for r in records
        )

    def test_custom_words_mixed_code_and_default(self) -> None:
        """混合：部分有 code，部分回退默认，按 placeholder 聚合记录"""
        cfg = PIIConfig(
            enable=True,
            custom_sensitive_words=[
                CustomWord(word="张伟", code="化名A"),
                CustomWord(word="李娜", code="化名B"),
                CustomWord(word="公司X"),
            ],
            custom_words_placeholder="[敏感]",
        )
        redactor = PIIRedactor(cfg)
        text = "张伟和李娜在公司X共事，公司X很大。"
        result, records = redactor.redact_snippet(text, None)
        assert "张伟" not in result
        assert "李娜" not in result
        assert "公司X" not in result
        assert "化名A" in result
        assert "化名B" in result
        assert "[敏感]" in result

        by_ph = {r.placeholder: r for r in records if r.kind == "custom_word"}
        assert by_ph["化名A"].count == 1
        assert by_ph["化名B"].count == 1
        assert by_ph["[敏感]"].count == 2

    def test_custom_word_length_desc_across_codes(self) -> None:
        """跨 code 按长度降序替换，避免短词吞掉长词前缀"""
        cfg = PIIConfig(
            enable=True,
            custom_sensitive_words=[
                CustomWord(word="张伟", code="A"),
                CustomWord(word="张伟强", code="B"),
            ],
        )
        redactor = PIIRedactor(cfg)
        # 如果先替换短词 "张伟"，"张伟强" 将永远匹配不到
        text = "张伟强和张伟都在现场"
        result, records = redactor.redact_snippet(text, None)
        assert "张伟强" not in result
        assert "张伟" not in result
        assert "B" in result
        assert "A" in result

        by_ph = {r.placeholder: r.count for r in records if r.kind == "custom_word"}
        assert by_ph.get("B") == 1
        assert by_ph.get("A") == 1
