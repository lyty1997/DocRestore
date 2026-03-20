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

"""云端 LLM 精修器（基于 litellm）"""

from __future__ import annotations

import litellm

from docrestore.llm.prompts import (
    build_refine_prompt,
    parse_gaps,
)
from docrestore.models import RefineContext, RefinedResult
from docrestore.pipeline.config import LLMConfig


class CloudLLMRefiner:
    """通过 litellm 调用云端 LLM（GLM/Claude/GPT 等）"""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    async def refine(
        self, raw_markdown: str, context: RefineContext
    ) -> RefinedResult:
        """精修单段 markdown。

        1. 构造 prompt messages
        2. litellm.acompletion 调用
        3. 解析 GAP 标记
        4. 返回 RefinedResult
        """
        messages = build_refine_prompt(raw_markdown, context)

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

        response = await litellm.acompletion(**kwargs)
        if not response.choices:
            msg = f"LLM 返回空 choices（model={self._config.model}）"
            raise RuntimeError(msg)
        content: str = response.choices[0].message.content or ""

        cleaned_md, gaps = parse_gaps(content)
        return RefinedResult(markdown=cleaned_md, gaps=gaps)
