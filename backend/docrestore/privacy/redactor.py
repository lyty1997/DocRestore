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
from docrestore.privacy.markup import split_protected
from docrestore.privacy.patterns import (
    redact_structured_pii,
    redact_tokens_only_pii,
)

logger = logging.getLogger(__name__)

# 实体替换安全阈值（issue #13）：NER 偶发会吐出单字、纯标点、文件名碎片或
# 整句作为"实体名"，一旦无差别全局替换会把正文/结构打碎，且不可逆。
_MIN_ENTITY_LEN = 2  # 短于此（单字"的"/"人"、单符号）一律跳过
_HIGH_FREQ_WARN = 50  # 单实体替换次数超此 → 疑似误检，告警（仍执行）
_MAX_ENTITY_LEN = 64  # 实体长度超此 → 疑似把整句当实体，**丢弃**（误检净化）

#: markup/结构字符：实体名含任一即非"名字"（如 `;'>kcat` / `U<` / `L)-aspartate`），
#: 用于词表净化把结构碎片挡在替换之外（详见 pii-entity-overredaction-fix.md §3-B2）。
_MARKUP_CHARS = frozenset("/\\<>${};'\"()[]|=`")

#: 以常见文件扩展名收尾的候选（如 `xxx.jpg`）一律丢弃——图片标识符不是人名/机构名。
_FILE_EXT_RE = re.compile(
    r"\.(?:jpe?g|png|gif|svg|bmp|tiff?|webp|pdf|md|txt)$", re.IGNORECASE,
)


@dataclass(frozen=True)
class EntityLexicon:
    """本地 NER 检测到的实体词典，用于复用（如 re-OCR 文本脱敏）"""

    person_names: tuple[str, ...]
    org_names: tuple[str, ...]


def _is_safe_entity(name: str) -> bool:
    """实体名是否可安全用于全局替换。

    过滤会把正文打碎的坏输出（issue #13）：空 / 过短（单字单符号）/
    纯标点。``str.isalnum()`` 对中文/日文等返回 True，故可借它排除纯标点。
    """
    if len(name) < _MIN_ENTITY_LEN:
        return False
    return any(ch.isalnum() for ch in name)


def _looks_like_name(name: str) -> bool:
    """词表净化：候选是否"像"人名/机构名，挡掉 NER 在结构/科技文本上的误检。

    在 :func:`_is_safe_entity` 基础上额外要求：长度不过整句、不含 markup/结构字符、
    不以文件扩展名收尾、字母（含 CJK，``str.isalpha`` 对中日韩返回 True）占非空白
    字符比例 ≥ 0.5。取舍为「召回换精度」：带数字/符号的真实机构名（极少见）会被放过，
    换取零结构误伤（详见 pii-entity-overredaction-fix.md §3-B2、§7-R2）。
    """
    if not _is_safe_entity(name):
        return False
    if len(name) > _MAX_ENTITY_LEN:
        return False
    if any(ch in _MARKUP_CHARS for ch in name):
        return False
    if _FILE_EXT_RE.search(name):
        return False
    non_space = [ch for ch in name if not ch.isspace()]
    if not non_space:
        return False
    letterish = sum(1 for ch in non_space if ch.isalpha())
    return letterish / len(non_space) >= 0.5


def _is_ascii_token(name: str) -> bool:
    """实体是否为纯 ASCII 词形（决定是否启用词边界匹配，CJK 无词边界故走精确串）。"""
    return name.isascii() and any(ch.isalnum() for ch in name)


def _sub_in_free(segment: str, name: str, placeholder: str) -> tuple[str, int]:
    """在单个自由文本段内替换实体，返回 (替换后, 次数)。

    纯 ASCII 实体加词边界 ``(?<![0-9A-Za-z])…(?![0-9A-Za-z])``，避免命中更长单词的
    子串（如 `FGR` 不再吃进 `FGRFP`）；含 CJK 的实体走精确串替换（CJK 无词边界）。
    """
    if _is_ascii_token(name):
        pattern = re.compile(
            r"(?<![0-9A-Za-z])" + re.escape(name) + r"(?![0-9A-Za-z])",
        )
        return pattern.subn(placeholder, segment)
    occurrences = segment.count(name)
    if occurrences == 0:
        return segment, 0
    return segment.replace(name, placeholder), occurrences


def _replace_entities(
    text: str,
    names: list[str],
    placeholder: str,
) -> tuple[str, int]:
    """结构感知 + 词边界的实体替换，返回 (替换后文本, 替换次数)。

    三重防护（pii-entity-overredaction-fix.md §3-A）：
    1. 词表净化 :func:`_looks_like_name` 丢弃结构碎片/文件名/整句等误检；
    2. :func:`split_protected` 只在自由文本段替换，保护图片 src / HTML 标签 /
       行内·围栏代码 / LaTeX / URL；
    3. 自由段内 ASCII 实体走词边界，防词内子串误命中。

    按长度降序处理，防"张三"先于"张三丰"匹配。异常高频实体告警（仍执行）。
    """
    candidates = [
        name
        for name in sorted(
            (n.strip() for n in names), key=len, reverse=True,
        )
        if _looks_like_name(name)
    ]
    if not candidates:
        return text, 0

    # 偶数下标=自由文本（可替），奇数下标=结构保护段（原样保留）。
    parts = split_protected(text)
    total = 0
    for name in candidates:
        name_count = 0
        for i in range(0, len(parts), 2):
            parts[i], replaced = _sub_in_free(parts[i], name, placeholder)
            name_count += replaced
        if name_count > _HIGH_FREQ_WARN:
            logger.warning(
                "实体替换次数异常高（%d 次 > %d），疑似 NER 误检，仍执行：%r",
                name_count, _HIGH_FREQ_WARN, name[:40],
            )
        total += name_count
    return "".join(parts), total


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

        供出云闸口（#67）统一兜底用。实体替换经词表净化 + 结构保护区切分 + ASCII
        词边界（:func:`_replace_entities`），对代码标识符 / import 路径 / 图片 src /
        HTML 标签 / LaTeX 等结构化文本安全（不会改坏结构，只在自由正文段替 lexicon 里的
        人名/机构串）。遵循 ``redact_person_name`` / ``redact_org_name`` 开关，
        幂等（占位符不被二次匹配）。
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
