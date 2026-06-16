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

"""PIIRedactor 核心逻辑：结构化 regex + 自定义敏感词 + 实体词表替换 → 不可逆替换。

实体词表由外部本地 NER（``PIIGuard.detect_entities``）提供，
本模块只按词表替换，不再调用云端 LLM 检测。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from docrestore.models import RedactionRecord
from docrestore.pipeline.config import PIIConfig
from docrestore.privacy.patterns import (
    redact_structured_pii,
    redact_tokens_only_pii,
)

logger = logging.getLogger(__name__)

# 实体替换安全阈值（issue #13）：LLM 偶发幻觉/格式错误会吐出单字、纯标点或
# 整句作为"实体名"，一旦全局 str.replace 会把正文打碎，且不可逆。
_MIN_ENTITY_LEN = 2  # 短于此（单字"的"/"人"、单符号）一律跳过
_HIGH_FREQ_WARN = 50  # 单实体替换次数超此 → 疑似误检，告警（仍执行）
_LONG_ENTITY_WARN = 64  # 实体长度超此 → 疑似把整句当实体，告警（仍执行）


@dataclass(frozen=True)
class EntityLexicon:
    """LLM 检测到的实体词典，用于复用（如 re-OCR 文本脱敏）"""

    person_names: tuple[str, ...]
    org_names: tuple[str, ...]


def _is_safe_entity(name: str) -> bool:
    """实体名是否可安全用于全局替换。

    过滤会把正文打碎的 LLM 坏输出（issue #13）：空 / 过短（单字单符号）/
    纯标点。``str.isalnum()`` 对中文/日文等返回 True，故可借它排除纯标点。
    """
    if len(name) < _MIN_ENTITY_LEN:
        return False
    return any(ch.isalnum() for ch in name)


def _replace_entities(
    text: str,
    names: list[str],
    placeholder: str,
) -> tuple[str, int]:
    """按长度降序替换实体名称，返回 (替换后文本, 替换次数)。

    按长度降序排列防止"张三"先于"张三丰"匹配。跳过空/过短/纯标点实体，
    对异常高频/超长实体告警，避免 LLM 坏输出全篇误替（issue #13）。
    """
    count = 0
    # 先 strip 再按长度降序，避免短实体先匹配
    sorted_names = sorted(
        (n.strip() for n in names), key=len, reverse=True,
    )
    for name in sorted_names:
        if not _is_safe_entity(name):
            continue
        occurrences = text.count(name)
        if occurrences == 0:
            continue
        if occurrences > _HIGH_FREQ_WARN:
            logger.warning(
                "实体替换次数异常高（%d 次 > %d），疑似 LLM 误检，仍执行：%r",
                occurrences, _HIGH_FREQ_WARN, name[:40],
            )
        if len(name) > _LONG_ENTITY_WARN:
            logger.warning(
                "实体长度异常（%d > %d），疑似把整句当实体，仍执行：%r",
                len(name), _LONG_ENTITY_WARN, name[:80],
            )
        text = text.replace(name, placeholder)
        count += occurrences
    return text, count


def _placeholder_split_re(placeholders: set[str]) -> re.Pattern[str] | None:
    """构造按占位符切分文本的正则（捕获组保留占位符本身）。

    长串优先（按长度降序拼 alternation），避免短占位符吞掉长占位符；无有效占位符
    返回 None。供自定义词替换把已插入占位符当「保护区」、不二次命中（#61 幂等）。
    """
    valid = sorted({p for p in placeholders if p}, key=len, reverse=True)
    if not valid:
        return None
    return re.compile("(" + "|".join(re.escape(p) for p in valid) + ")")


class PIIRedactor:
    """PII 脱敏器：regex 结构化 PII + LLM 实体检测"""

    def __init__(self, config: PIIConfig) -> None:
        """初始化脱敏器。"""
        self._config = config

    def redact_regex_only(
        self,
        text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """仅做结构化 regex（手机/邮箱/身份证/银行卡）+ 自定义敏感词。

        不调用 LLM，不依赖 EntityLexicon。用于流式 Pipeline 的 OCR Producer
        逐页调用（此时实体检测尚未发生，lexicon 还没拿到）。
        """
        return self.redact_snippet(text, lexicon=None)

    def redact_tokens_only(
        self,
        text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """代码正文档位：仅高置信密钥 token + 自定义敏感词，零误伤正常代码。

        不跑 KV / 手机 / 邮箱 / 卡 / host / url 全量正则（会把 ``password =
        get_secret()`` 改坏），不做实体替换（保护 import 路径 / 标识符）。幂等。
        """
        text, records = redact_tokens_only_pii(text, self._config)
        text, custom_records = self._replace_custom_words(text)
        records.extend(custom_records)
        return text, records

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

    def apply_lexicon(
        self,
        text: str,
        lexicon: EntityLexicon,
    ) -> tuple[str, list[RedactionRecord]]:
        """仅按实体词典替换人名/机构名，**不跑结构化 regex**，返回 (文本, 记录)。

        供出云闸口（#67）统一兜底用：实体替换是精确串替换，对代码标识符 / import
        路径 / 结构化文本一律安全（只替 lexicon 里的人名/机构串）。遵循
        ``redact_person_name`` / ``redact_org_name`` 开关，幂等（占位符不被二次匹配）。
        """
        return self._apply_lexicon(text, lexicon)

    def _replace_custom_words(
        self,
        text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """替换用户自定义的敏感词。

        每个 CustomWord 可指定独立 code 作为替换符；code 为空则回退到
        custom_words_placeholder。按 placeholder 聚合为 RedactionRecord，
        多代号场景下会产生多条记录。替换顺序按 word 长度全局降序，
        避免短词先吞掉长词的前缀（如"张伟"先于"张伟强"）。

        幂等（#61）：把已存在的占位符视为「保护区」，只在占位符之外的自由文本里
        替换——否则当敏感词是其占位符子串时（如 word="PII"、placeholder=
        "[PII_REDACTED]"），对重叠文本反复脱敏（分段精修 + 输出兜底会多次 redact
        同一段）会在已插入占位符内部二次命中并破坏它。
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
        if not sorted_entries:
            return text, records

        counts: dict[str, int] = {}

        def _redact_free(segment: str) -> str:
            for entry in sorted_entries:
                placeholder = entry.code or default_ph
                occurrences = segment.count(entry.word)
                if occurrences > 0:
                    segment = segment.replace(entry.word, placeholder)
                    counts[placeholder] = counts.get(placeholder, 0) + occurrences
            return segment

        split_re = _placeholder_split_re(
            {e.code or default_ph for e in sorted_entries},
        )
        if split_re is None:
            text = _redact_free(text)
        else:
            # split 后奇数段是占位符（原样保留），偶数段是自由文本（执行替换）
            text = "".join(
                part if i % 2 == 1 else _redact_free(part)
                for i, part in enumerate(split_re.split(text))
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
