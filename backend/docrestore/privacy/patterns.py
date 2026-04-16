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

"""结构化 PII 正则检测与替换

处理顺序：身份证 → 邮箱 → 手机号 → 银行卡
（避免 18 位身份证被银行卡候选吞掉）
"""

from __future__ import annotations

import re

from docrestore.models import RedactionRecord
from docrestore.pipeline.config import PIIConfig

# --- 正则模式 ---

# 手机号：+86 前缀可选，1[3-9] 开头，中间可有空格/短横线
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?!\d)"
)

# 邮箱
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

# 身份证号（18 位）：6 位地址码 + 出生日期 + 3 位顺序码 + 校验位
_ID_CARD_RE = re.compile(
    r"(?<!\d)"
    r"[1-9]\d{5}"
    r"(?:19|20)\d{2}"
    r"(?:0[1-9]|1[0-2])"
    r"(?:0[1-9]|[12]\d|3[01])"
    r"\d{3}[\dXx]"
    r"(?!\d)"
)

# 银行卡号（16-19 位数字，中间可有空格/短横线）
_BANK_CARD_RE = re.compile(
    r"(?<!\d)"
    r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"
    r"(?:[-\s]?\d{1,3})?"
    r"(?!\d)"
)


def _normalize_digits(raw: str) -> str:
    """去掉空格和短横线，只保留数字和 Xx。"""
    return re.sub(r"[-\s]", "", raw)


def _is_valid_phone(raw: str) -> bool:
    """归一化后验证手机号格式。"""
    digits = _normalize_digits(raw)
    # 去掉可能的 +86 前缀
    if digits.startswith("+86"):
        digits = digits[3:]
    elif digits.startswith("86"):
        digits = digits[2:]
    return bool(re.fullmatch(r"1[3-9]\d{9}", digits))


def _luhn_check(card_number: str) -> bool:
    """Luhn 校验算法验证银行卡号。"""
    digits = [int(d) for d in card_number]
    # 从右向左，偶数位（从 1 开始计数）乘以 2
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def redact_structured_pii(
    text: str,
    config: PIIConfig,
) -> tuple[str, list[RedactionRecord]]:
    """正则替换结构化 PII，返回 (脱敏文本, 记录列表)。

    处理顺序：身份证 → 邮箱 → 手机号 → 银行卡。
    已替换位置用占位符标记，后续模式不会匹配到占位符。
    """
    records: list[RedactionRecord] = []

    # 1. 身份证号
    if config.redact_id_card:
        text, count = _replace_id_card(text, config)
        if count > 0:
            records.append(
                RedactionRecord(
                    kind="id_card",
                    method="regex",
                    placeholder=config.id_card_placeholder,
                    count=count,
                )
            )

    # 2. 邮箱
    if config.redact_email:
        text, count = _replace_email(text, config)
        if count > 0:
            records.append(
                RedactionRecord(
                    kind="email",
                    method="regex",
                    placeholder=config.email_placeholder,
                    count=count,
                )
            )

    # 3. 手机号
    if config.redact_phone:
        text, count = _replace_phone(text, config)
        if count > 0:
            records.append(
                RedactionRecord(
                    kind="phone",
                    method="regex",
                    placeholder=config.phone_placeholder,
                    count=count,
                )
            )

    # 4. 银行卡号
    if config.redact_bank_card:
        text, count = _replace_bank_card(text, config)
        if count > 0:
            records.append(
                RedactionRecord(
                    kind="bank_card",
                    method="regex",
                    placeholder=config.bank_card_placeholder,
                    count=count,
                )
            )

    return text, records


def _replace_id_card(
    text: str, config: PIIConfig,
) -> tuple[str, int]:
    """替换身份证号。"""
    count = 0

    def _repl(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return config.id_card_placeholder

    text = _ID_CARD_RE.sub(_repl, text)
    return text, count


def _replace_email(
    text: str, config: PIIConfig,
) -> tuple[str, int]:
    """替换邮箱。"""
    count = 0

    def _repl(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return config.email_placeholder

    text = _EMAIL_RE.sub(_repl, text)
    return text, count


def _replace_phone(
    text: str, config: PIIConfig,
) -> tuple[str, int]:
    """替换手机号。"""
    count = 0

    def _repl(m: re.Match[str]) -> str:
        nonlocal count
        if _is_valid_phone(m.group()):
            count += 1
            return config.phone_placeholder
        return m.group()

    text = _PHONE_RE.sub(_repl, text)
    return text, count


def _replace_bank_card(
    text: str, config: PIIConfig,
) -> tuple[str, int]:
    """替换银行卡号（需通过 Luhn 校验）。"""
    count = 0

    def _repl(m: re.Match[str]) -> str:
        nonlocal count
        digits = _normalize_digits(m.group())
        if len(digits) < 16 or not _luhn_check(digits):
            return m.group()
        count += 1
        return config.bank_card_placeholder

    text = _BANK_CARD_RE.sub(_repl, text)
    return text, count
