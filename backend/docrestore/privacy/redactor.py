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

"""PIIRedactor 核心逻辑：regex + LLM 实体检测 → 不可逆替换"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from docrestore.llm.base import LLMRefiner
from docrestore.models import RedactionRecord
from docrestore.pipeline.config import PIIConfig
from docrestore.privacy.patterns import redact_structured_pii

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityLexicon:
    """LLM 检测到的实体词典，用于复用（如 re-OCR 文本脱敏）"""

    person_names: tuple[str, ...]
    org_names: tuple[str, ...]


def _replace_entities(
    text: str,
    names: list[str],
    placeholder: str,
) -> tuple[str, int]:
    """按长度降序替换实体名称，返回 (替换后文本, 替换次数)。

    按长度降序排列防止"张三"先于"张三丰"匹配。
    """
    count = 0
    # 按长度降序排序，避免短实体先匹配
    sorted_names = sorted(names, key=len, reverse=True)
    for name in sorted_names:
        if not name:
            continue
        occurrences = text.count(name)
        if occurrences > 0:
            text = text.replace(name, placeholder)
            count += occurrences
    return text, count


class PIIRedactor:
    """PII 脱敏器：regex 结构化 PII + LLM 实体检测"""

    def __init__(self, config: PIIConfig) -> None:
        """初始化脱敏器。"""
        self._config = config

    async def redact_for_cloud(
        self,
        text: str,
        refiner: LLMRefiner | None,
    ) -> tuple[str, list[RedactionRecord], EntityLexicon | None]:
        """完整脱敏：regex → LLM 实体检测 → 实体替换。

        返回 (脱敏文本, 脱敏记录, 实体词典)。
        实体词典可传给 redact_snippet() 复用。
        """
        # 1. regex 替换结构化 PII
        text, records = redact_structured_pii(
            text, self._config,
        )

        # 2. LLM 实体检测（输入已去结构化 PII 的文本）
        lexicon: EntityLexicon | None = None
        needs_person = self._config.redact_person_name
        needs_org = self._config.redact_org_name

        if (needs_person or needs_org) and refiner is not None:
            try:
                person_names, org_names = (
                    await refiner.detect_pii_entities(text)
                )
                lexicon = EntityLexicon(
                    person_names=tuple(person_names),
                    org_names=tuple(org_names),
                )
            except Exception:
                logger.warning(
                    "PII 实体检测失败",
                    exc_info=True,
                )

        # 3. 用 lexicon 做实体替换
        if lexicon is not None:
            text, entity_records = self._apply_lexicon(
                text, lexicon,
            )
            records.extend(entity_records)

        # 4. 自定义敏感词替换
        text, custom_records = self._replace_custom_words(text)
        records.extend(custom_records)

        return text, records, lexicon

    def redact_snippet(
        self,
        text: str,
        lexicon: EntityLexicon | None,
    ) -> tuple[str, list[RedactionRecord]]:
        """轻量脱敏（regex + 复用 lexicon），用于 re-OCR 文本。

        不调用 LLM。
        """
        # regex 替换
        text, records = redact_structured_pii(
            text, self._config,
        )

        # 复用已有 lexicon
        if lexicon is not None:
            text, entity_records = self._apply_lexicon(
                text, lexicon,
            )
            records.extend(entity_records)

        # 自定义敏感词替换
        text, custom_records = self._replace_custom_words(text)
        records.extend(custom_records)

        return text, records

    def _replace_custom_words(
        self,
        text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """替换用户自定义的敏感词。

        每个 CustomWord 可指定独立 code 作为替换符；code 为空则回退到
        custom_words_placeholder。按 placeholder 聚合为 RedactionRecord，
        多代号场景下会产生多条记录。替换顺序按 word 长度全局降序，
        避免短词先吞掉长词的前缀（如"张伟"先于"张伟强"）。
        """
        records: list[RedactionRecord] = []
        words = self._config.custom_sensitive_words
        if not words:
            return text, records

        default_ph = self._config.custom_words_placeholder
        # 全局按 word 长度降序，每个词替换时使用自己的 placeholder
        sorted_entries = sorted(
            (e for e in words if e.word),
            key=lambda e: len(e.word),
            reverse=True,
        )
        counts: dict[str, int] = {}
        for entry in sorted_entries:
            placeholder = entry.code or default_ph
            occurrences = text.count(entry.word)
            if occurrences > 0:
                text = text.replace(entry.word, placeholder)
                counts[placeholder] = (
                    counts.get(placeholder, 0) + occurrences
                )

        for placeholder, count in counts.items():
            records.append(
                RedactionRecord(
                    kind="custom_word",
                    method="exact_match",
                    placeholder=placeholder,
                    count=count,
                )
            )

        return text, records

    def _apply_lexicon(
        self,
        text: str,
        lexicon: EntityLexicon,
    ) -> tuple[str, list[RedactionRecord]]:
        """用实体词典替换文本中的人名和机构名。"""
        records: list[RedactionRecord] = []

        if self._config.redact_person_name:
            text, count = _replace_entities(
                text,
                list(lexicon.person_names),
                self._config.person_name_placeholder,
            )
            if count > 0:
                records.append(
                    RedactionRecord(
                        kind="person_name",
                        method="llm",
                        placeholder=(
                            self._config.person_name_placeholder
                        ),
                        count=count,
                    )
                )

        if self._config.redact_org_name:
            text, count = _replace_entities(
                text,
                list(lexicon.org_names),
                self._config.org_name_placeholder,
            )
            if count > 0:
                records.append(
                    RedactionRecord(
                        kind="org_name",
                        method="llm",
                        placeholder=(
                            self._config.org_name_placeholder
                        ),
                        count=count,
                    )
                )

        return text, records
