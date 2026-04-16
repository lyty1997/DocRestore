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

"""LLM 精修器接口与公共实现。

- LLMRefiner：Protocol，定义精修器对外暴露的全部能力。
- BaseLLMRefiner：基于 litellm 的公共实现，云端/本地实现共享。
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

import litellm

from docrestore.llm.prompts import (
    GAP_FILL_EMPTY_MARKER,
    build_doc_boundary_detect_prompt,
    build_final_refine_prompt,
    build_gap_fill_prompt,
    build_refine_prompt,
    parse_gaps,
)
from docrestore.models import DocBoundary, Gap, RefineContext, RefinedResult
from docrestore.pipeline.config import LLMConfig

logger = logging.getLogger(__name__)


class LLMRefiner(Protocol):
    """LLM 精修器接口"""

    async def refine(
        self, raw_markdown: str, context: RefineContext,
    ) -> RefinedResult:
        """精修单段：修复格式 + 检测缺口 + 还原结构，不改写内容含义。"""
        ...

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str:
        """从 re-OCR 文本中提取 gap 缺失内容。"""
        ...

    async def final_refine(self, markdown: str) -> RefinedResult:
        """整篇文档级精修：去除跨段重复和页眉水印。"""
        ...

    async def detect_doc_boundaries(
        self, merged_markdown: str,
    ) -> list[DocBoundary]:
        """检测合并文本中的文档边界。"""
        ...

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """检测文本中的人名和机构名，返回 (person_names, org_names)。

        本地实现可返回 ([], [])，云端实现应调用 LLM 做实体识别。
        检测失败抛异常，由调用方决定是否阻断云端调用。
        """
        ...


class BaseLLMRefiner:
    """LLM 精修器公共实现（litellm 调用）。

    detect_pii_entities 默认返回空列表（本地 LLM 场景无需检测）；
    云端实现 CloudLLMRefiner 覆盖此方法做真实实体识别。
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def _build_kwargs(
        self, messages: list[dict[str, str]],
    ) -> dict[str, object]:
        """构造 litellm.acompletion 公共参数。"""
        kwargs: dict[str, object] = {
            "model": self._config.model,
            "messages": messages,
            "num_retries": self._config.max_retries,
            "timeout": self._config.timeout,
        }
        if self._config.api_base:
            kwargs["base_url"] = self._config.api_base
        if self._config.api_key:
            kwargs["api_key"] = self._config.api_key
        return kwargs

    async def refine(
        self, raw_markdown: str, context: RefineContext,
    ) -> RefinedResult:
        """精修单段 markdown。

        1. 构造 prompt messages
        2. litellm.acompletion 调用
        3. 解析 GAP 标记
        4. 返回 RefinedResult
        """
        messages = build_refine_prompt(raw_markdown, context)
        kwargs = self._build_kwargs(messages)

        response = await litellm.acompletion(**kwargs)
        if not response.choices:
            msg = f"LLM 返回空 choices（model={self._config.model}）"
            raise RuntimeError(msg)
        choice = response.choices[0]
        content: str = choice.message.content or ""
        truncated = getattr(choice, "finish_reason", None) == "length"

        if truncated:
            logger.warning(
                "LLM 输出因 token 上限被截断（model=%s, finish_reason=length）",
                self._config.model,
            )

        cleaned_md, gaps = parse_gaps(content)
        return RefinedResult(markdown=cleaned_md, gaps=gaps, truncated=truncated)

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str:
        """从 re-OCR 文本中提取 gap 缺失内容。

        返回提取到的内容片段，空字符串表示无法填充。
        """
        messages = build_gap_fill_prompt(
            gap, current_page_text, next_page_text, next_page_name,
        )
        kwargs = self._build_kwargs(messages)

        response = await litellm.acompletion(**kwargs)
        if not response.choices:
            msg = f"LLM 返回空 choices（model={self._config.model}）"
            raise RuntimeError(msg)

        fill_content: str = response.choices[0].message.content or ""

        if GAP_FILL_EMPTY_MARKER in fill_content.strip():
            return ""

        return fill_content.strip()

    async def final_refine(
        self, markdown: str,
    ) -> RefinedResult:
        """整篇文档级精修：去除跨段重复和页眉水印。"""
        messages = build_final_refine_prompt(markdown)
        kwargs = self._build_kwargs(messages)

        response = await litellm.acompletion(**kwargs)
        if not response.choices:
            msg = (
                "LLM 返回空 choices"
                f"（model={self._config.model}）"
            )
            raise RuntimeError(msg)
        choice = response.choices[0]
        content: str = choice.message.content or ""
        truncated = getattr(choice, "finish_reason", None) == "length"

        if truncated:
            logger.warning(
                "LLM 整篇精修输出因 token 上限被截断"
                "（model=%s, finish_reason=length）",
                self._config.model,
            )

        cleaned_md, gaps = parse_gaps(content)
        return RefinedResult(markdown=cleaned_md, gaps=gaps, truncated=truncated)

    async def detect_doc_boundaries(
        self, merged_markdown: str,
    ) -> list[DocBoundary]:
        """检测合并文本中的文档边界。"""
        messages = build_doc_boundary_detect_prompt(merged_markdown)
        kwargs = self._build_kwargs(messages)

        response = await litellm.acompletion(**kwargs)
        if not response.choices:
            logger.warning("文档边界检测返回空 choices，假定单文档")
            return []

        content: str = response.choices[0].message.content or "[]"
        try:
            data = json.loads(content.strip())
            if not isinstance(data, list):
                logger.warning("文档边界检测返回非数组，假定单文档")
                return []

            boundaries: list[DocBoundary] = []
            for item in data:
                if isinstance(item, dict):
                    after_page = item.get("after_page", "")
                    new_title = item.get("new_title", "")
                    if after_page:
                        boundaries.append(
                            DocBoundary(
                                after_page=str(after_page),
                                new_title=str(new_title),
                            )
                        )
            return boundaries
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("文档边界检测 JSON 解析失败: %s，假定单文档", e)
            return []

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """默认实现：不做实体检测，返回空列表。

        本地 LLM 实现继承此默认行为（数据不出本地，无需识别）。
        云端实现应覆盖此方法调用 LLM 做真实识别。
        """
        _ = text
        return [], []
