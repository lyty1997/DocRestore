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

"""本地 LLM 精修器（通过 OpenAI 兼容 API）"""

from __future__ import annotations

from docrestore.llm.base import BaseLLMRefiner


class LocalLLMRefiner(BaseLLMRefiner):
    """本地 LLM 精修器（ollama/vllm/llama.cpp 等 OpenAI 兼容服务）。

    与 CloudLLMRefiner 共享 refine/fill_gap/final_refine，
    detect_pii_entities 继承 BaseLLMRefiner 的空实现（本地场景数据不出本地）。
    """
