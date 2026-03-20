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

"""LLM 精修器 Protocol 定义"""

from __future__ import annotations

from typing import Protocol

from docrestore.models import RefineContext, RefinedResult


class LLMRefiner(Protocol):
    """LLM 精修器接口"""

    async def refine(
        self, raw_markdown: str, context: RefineContext
    ) -> RefinedResult:
        """精修单段：修复格式 + 检测缺口 + 还原结构，不改写内容含义。"""
        ...
