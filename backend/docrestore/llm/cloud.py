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

"""云端 LLM 精修器（基于 BaseLLMRefiner，走 litellm）。"""

from __future__ import annotations

from docrestore.llm.base import BaseLLMRefiner


class CloudLLMRefiner(BaseLLMRefiner):
    """云端 LLM 精修器。

    与 BaseLLMRefiner 共享 refine/fill_gap/final_refine；作为 ``provider="cloud"``
    的精修器类型标识（Pipeline._create_refiner 据 provider 选型）。

    历史上曾覆盖 ``detect_pii_entities`` 做云端 PII 实体识别，S3 起人名/机构名
    检测改本地 NER（``privacy/ner.py::PIIGuard.detect_entities``，名字不出本机），
    该云端检测路径已于 S4 删除。
    """
