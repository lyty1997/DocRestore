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

"""云端 LLM 精修器：在 BaseLLMRefiner 基础上增加 PII 实体检测。"""

from __future__ import annotations

import json

from docrestore.llm.base import BaseLLMRefiner
from docrestore.llm.prompts import build_pii_detect_prompt


class CloudLLMRefiner(BaseLLMRefiner):
    """云端 LLM 精修器，额外支持 PII 实体检测。"""

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """检测文本中的人名和机构名。

        返回 (person_names, org_names)。
        解析失败抛 RuntimeError。
        """
        messages = build_pii_detect_prompt(text)
        kwargs = self._build_kwargs(messages)

        response = await self._call_llm(kwargs)
        if not response.choices:
            msg = (
                "LLM 返回空 choices"
                f"（model={self._config.model}）"
            )
            raise RuntimeError(msg)

        raw: str = response.choices[0].message.content or ""

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            msg = f"PII 实体检测返回非 JSON: {raw[:200]}"
            raise RuntimeError(msg) from exc

        person_names: list[str] = list(
            data.get("person_names", []),
        )
        org_names: list[str] = list(
            data.get("org_names", []),
        )
        return person_names, org_names
