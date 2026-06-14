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

"""PIIGuard：PII 脱敏统一闸口。

把原本分散在 producer / 代码路径 / 各云端边界的脱敏逻辑收进**单一抽象**，模式
分支（文档/代码/PPT）里不再各写各的脱敏代码——#36 的三个洞正是「散」的代价。
设计见 ``docs/zh/backend/pii-unification.md``。

``profile="full"`` 包住现有 ``PIIRedactor`` 的行为（与现状逐字节一致）；
``profile="tokens_only"``（**S2** 落地）给代码正文用，仅拦高置信密钥
（``sk-``/``gh?_``/``AKIA``/JWT）+ 自定义词，零误伤正常代码；本地 NER 取代
云端实体检测在 **S3** 落地。
"""

from __future__ import annotations

import logging
from typing import Literal

from docrestore.pipeline.config import PIIConfig
from docrestore.privacy.ner import NERUnavailableError, get_detector
from docrestore.privacy.redactor import EntityLexicon, PIIRedactor

logger = logging.getLogger(__name__)

#: 结构化脱敏档位。``full``=全量正则+自定义词（文档/PPT、代码头部）；
#: ``tokens_only``=仅高置信密钥+自定义词（代码正文，S2 落地，零误伤代码）。
RedactProfile = Literal["full", "tokens_only"]


class PIIGuard:
    """PII 脱敏统一闸口。请求级 ``pii_cfg`` 构造，贯穿整个任务。

    三个职责（S1 先落前两个的 ``full`` 档）：
    - ``redact_structured(text, profile)``：纯本地正则结构化脱敏（不调用 LLM/NER）。
    - ``redact_for_cloud(text, lexicon, profile)``：送任何云端调用前的统一闸口
      （结构化 + 可选实体替换）。
    - ``detect_entities``：人名/机构名检测，S3 改本地 NER（当前仍由 Pipeline 侧
      ``_detect_entities`` 委托配置的 refiner，故本类暂不含）。
    """

    def __init__(self, pii_cfg: PIIConfig) -> None:
        """用请求级 PII 配置构造。"""
        self._cfg = pii_cfg
        self._redactor = PIIRedactor(pii_cfg)

    @property
    def enabled(self) -> bool:
        """是否启用 PII 脱敏（``pii_cfg.enable``）。"""
        return self._cfg.enable

    def redact_structured(
        self, text: str, *, profile: RedactProfile = "full",
    ) -> str:
        """本地正则结构化脱敏（+ 自定义敏感词），不调用 LLM/NER，幂等。

        ``profile="full"``：全量（手机/邮箱/证件/卡/凭据/host/内链 + 自定义词），
        给文档/PPT producer 逐页与代码头部注释用。
        ``profile="tokens_only"``：仅高置信密钥（``sk-``/``AKIA``/JWT）+ 自定义词，
        给代码正文用——绝不误伤 ``password = get_secret()`` 这类正常代码。
        """
        if profile == "tokens_only":
            text_out, _ = self._redactor.redact_tokens_only(text)
            return text_out
        text_out, _ = self._redactor.redact_regex_only(text)
        return text_out

    def redact_for_cloud(
        self,
        text: str,
        lexicon: EntityLexicon | None = None,
        *,
        profile: RedactProfile = "full",
    ) -> str:
        """送云端调用前的统一脱敏闸口：结构化（按 profile）+ 实体（lexicon 非空）。

        所有云端调用点（分段精修 / gap-fill / PPT 每页 / code refine·repair·audit /
        file_path·片段·诊断）都应只走此闸口。幂等：对已脱敏文本再调安全（占位符不
        被二次匹配）。``lexicon=None`` 时退化为仅结构化（代码路径用，正文不替实体以
        保护标识符）。
        """
        if profile == "tokens_only":
            # tokens_only 不做实体替换（保护标识符），lexicon 被忽略
            text_out, _ = self._redactor.redact_tokens_only(text)
            return text_out
        text_out, _ = self._redactor.redact_snippet(text, lexicon)
        return text_out

    def detect_entities(self, text: str) -> EntityLexicon | None:
        """本地 NER 检测人名/机构名 → EntityLexicon；不检测 / 检测失败 → None。

        取代原 ``refiner.detect_pii_entities`` 的云端检测（名字不再出本机）。返回
        None 的三种情形：① 未请求实体脱敏（``redact_person_name``/``org`` 全 False）；
        ② ``ner_backend="none"``（用户显式关本地 NER，属知情放弃——上层不应据此阻断
        云端）；③ 检测异常（库不可用 / 模型崩溃）——上层据此按
        ``block_cloud_on_detect_failure`` fail-closed，绝不放未脱敏名字上云。检测成功
        （含没找到任何实体）→ ``EntityLexicon``（person/org 可能为空元组）。
        """
        cfg = self._cfg
        if not (cfg.redact_person_name or cfg.redact_org_name):
            return None
        if cfg.ner_backend == "none":
            return None
        try:
            persons, orgs = get_detector(cfg.ner_models).detect(text)
        except NERUnavailableError:
            logger.warning("本地 NER 不可用，实体检测跳过（上层将 fail-closed）")
            return None
        except Exception:  # 运行期模型异常 → 同失败处理，fail-closed
            logger.warning("本地 NER 检测异常，实体检测跳过", exc_info=True)
            return None
        return EntityLexicon(
            person_names=tuple(persons), org_names=tuple(orgs),
        )
