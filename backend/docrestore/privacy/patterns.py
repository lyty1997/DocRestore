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

处理顺序：凭据/token → 身份证 → 邮箱 → user@host 连接目标 → 手机号
→ 银行卡 → 内部 URL（避免 18 位身份证被银行卡候选吞掉；邮箱先于 host
吃掉带 TLD 的 user@domain.tld，host 只接 IP 与无 TLD 主机名残留）。
"""

from __future__ import annotations

import ipaddress
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

# 凭据键值对：label 锚定（password=/token:/账号: 等），只替换 value、保留 label。
# label 全锚定，避免误伤普通词；value 支持引号包裹，未包裹时止于空白/引号/分隔符。
# 不收纳裸 ``key``/``user``（"key features"/"user manual" 等高频正文误报）。
_CRED_KV_RE = re.compile(
    r"(?P<key>password|passwd|pwd|secret(?:[_-]?key)?|api[_-]?key|access[_-]?key"
    r"|token|username|account|密码|口令|私钥|密钥|用户名|账号|账户)"
    r"(?P<sep>\s*[:：=]\s*)"
    r"""(?P<val>"[^"\n]*"|'[^'\n]*'|[^\s'";,&]+)""",
    re.IGNORECASE,
)

# URL 内联凭据：scheme://user:pass@host —— 替换 user:pass，保留 scheme 和 @host。
_URL_CRED_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)"
    r"(?P<cred>[^/\s:@]+:[^/\s:@]+)@"
)

# 已知高置信 token 格式（无需 label）：OpenAI sk- / GitHub gh?_ / AWS AKIA / JWT。
# 前置非字母数字断言防止匹配到 ``task-xxxx`` 之类词内子串。
_TOKEN_FORMAT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:"
    r"sk-[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")"
)

# user@host 连接目标（scp/ssh/rsync 目标，无 scheme / 无 password）。user 常含
# 人名（如 qiangming@host），整体脱。只接两类：user@IPv4、user@单 label 主机名
# （无点）。**带点的 FQDN / 邮箱域名（user@a.b.com）一律不收**——交给邮箱步骤；
# 这样即便用户关掉 redact_email，user@domain.com 也不会被 host 误切成
# [主机地址].com。主机名分支末尾 lookahead 拦住 FQDN 前缀；lookbehind 排除
# @/./-/词字符，避免匹配 a.b@c 中段或路径内片段。
_HOST_TARGET_RE = re.compile(
    r"(?<![\w@./-])"
    r"[A-Za-z0-9_][A-Za-z0-9._-]*@"
    r"(?:"
    r"\d{1,3}(?:\.\d{1,3}){3}"                      # IPv4
    r"|[A-Za-z][A-Za-z0-9-]*(?![A-Za-z0-9.-])"      # 单 label 主机名（无 TLD）
    r")"
    r"(?::\d{1,5})?"                               # 可选端口
)

# 内部 URL：私有内网 IP（10/172.16-31/192.168/127）的 URL 一律脱；host 命中
# sensitive_url_domains（用户配置后缀，如 antfin.com）的也脱。支持无 scheme 的
# 裸域名（OCR 文档常无 http://）。回调按 host 判定，非私有 / 非敏感域名原样保留，
# 不误伤公网链接。host 域名分支要求 ≥1 个点（FQDN），避免吞普通词。
_URL_LIKE_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)?"
    r"(?P<host>"
    r"\[[0-9A-Fa-f:.]+\]"                       # [IPv6] 含 :: 压缩 / IPv4-mapped
    r"|\d{1,3}(?:\.\d{1,3}){3}"                 # IPv4
    r"|[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"  # FQDN（≥1 点）
    r")"
    r"(?::\d{1,5})?"
    r"(?:[/?#][^\s\"'<>)）】\]]*)?"
)


def _host_matches_domains(host: str, domains: list[str]) -> bool:
    """host 等于某敏感域名或为其子域（大小写无关的后缀匹配）。"""
    host_l = host.lower().rstrip(".")
    for raw in domains:
        d = raw.strip().lower().lstrip(".")
        if not d:
            continue
        if host_l == d or host_l.endswith("." + d):
            return True
    return False


def _is_internal_ip(host: str) -> bool:
    """host 是私有 / 回环 IP（IPv4 或 ``[IPv6]``）。非 IP 形态返回 False。"""
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]  # 去 URL 里的 IPv6 方括号
    candidate = candidate.split("%", 1)[0]  # 去 IPv6 scope id（fe80::1%eth0）
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


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

    处理顺序：凭据/token → 身份证 → 邮箱 → user@host → 手机号 → 银行卡
    → 内部 URL。凭据先行避免 ``password=13800001111`` 的值被当手机号等误分类；
    邮箱先于 host，吃掉带 TLD 的 ``user@domain.tld``（host 只接 IP 与无 TLD 主机名
    残留）；内部 URL 最后，host step 已先把 ``user@IP`` 整体脱掉。已替换位置为
    占位符，后续模式不会再匹配到。
    """
    records: list[RedactionRecord] = []

    # (是否启用, 替换函数 (text, config) -> (text, count), 记录 kind, 占位符)
    steps = (
        (
            config.redact_credential, _replace_credentials,
            "credential", config.credential_placeholder,
        ),
        (
            config.redact_id_card, _replace_id_card,
            "id_card", config.id_card_placeholder,
        ),
        (
            config.redact_email, _replace_email,
            "email", config.email_placeholder,
        ),
        (
            config.redact_host, _replace_host,
            "host", config.host_placeholder,
        ),
        (
            config.redact_phone, _replace_phone,
            "phone", config.phone_placeholder,
        ),
        (
            config.redact_bank_card, _replace_bank_card,
            "bank_card", config.bank_card_placeholder,
        ),
        (
            config.redact_internal_url, _replace_internal_url,
            "internal_url", config.internal_url_placeholder,
        ),
    )

    for enabled, replace_fn, kind, placeholder in steps:
        if not enabled:
            continue
        text, count = replace_fn(text, config)
        if count > 0:
            records.append(
                RedactionRecord(
                    kind=kind, method="regex",
                    placeholder=placeholder, count=count,
                )
            )

    return text, records


def redact_tokens_only_pii(
    text: str,
    config: PIIConfig,
) -> tuple[str, list[RedactionRecord]]:
    """仅替换高置信密钥 token（``sk-`` / ``gh?_`` / ``AKIA`` / JWT），其余正则不跑。

    给**代码正文**用：固定前缀 + 20+ 字符的 token 格式碰不到正常代码，却拦得住
    硬编码 API key；而 KV 凭据 / 手机 / 邮箱 / 卡 / host / url 等全量正则会把
    ``password = get_secret()`` 之类正常代码改坏（见 pii-unification.md §4.2），
    故正文不跑。受 ``config.redact_credential`` 开关控制（token 属凭据类）。
    """
    if not config.redact_credential:
        return text, []
    count = 0

    def _repl(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return config.credential_placeholder

    text = _TOKEN_FORMAT_RE.sub(_repl, text)
    records: list[RedactionRecord] = []
    if count > 0:
        records.append(
            RedactionRecord(
                kind="credential", method="regex",
                placeholder=config.credential_placeholder, count=count,
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


def _replace_credentials(
    text: str, config: PIIConfig,
) -> tuple[str, int]:
    """替换凭据：label 锚定键值对 + URL 内联凭据 + 已知 token 格式。

    键值对只替换 value、保留 label；URL 只替换 user:pass；token 整段替换。
    顺序：先键值对（避免其 value 吞掉后续替换出的占位符），再 URL，再 token。
    """
    count = 0

    def _kv_repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return (
            f"{m.group('key')}{m.group('sep')}"
            f"{config.credential_placeholder}"
        )

    def _url_repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{m.group('scheme')}{config.credential_placeholder}@"

    def _token_repl(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return config.credential_placeholder

    text = _CRED_KV_RE.sub(_kv_repl, text)
    text = _URL_CRED_RE.sub(_url_repl, text)
    text = _TOKEN_FORMAT_RE.sub(_token_repl, text)
    return text, count


def _replace_host(
    text: str, config: PIIConfig,
) -> tuple[str, int]:
    """替换 user@host 连接目标（user@IP / user@主机名），整体含 user 一并脱。"""
    count = 0

    def _repl(_m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return config.host_placeholder

    text = _HOST_TARGET_RE.sub(_repl, text)
    return text, count


def _replace_internal_url(
    text: str, config: PIIConfig,
) -> tuple[str, int]:
    """替换内部 URL：私有 IP 的 URL + host 命中敏感域名后缀的 URL。

    非私有 / 非敏感域名的 URL 原样保留（回调返回 ``m.group(0)``），不误伤公网链接。
    """
    count = 0
    domains = config.sensitive_url_domains

    def _repl(m: re.Match[str]) -> str:
        nonlocal count
        host = m.group("host")
        if _is_internal_ip(host) or _host_matches_domains(host, domains):
            count += 1
            return config.internal_url_placeholder
        return m.group(0)

    text = _URL_LIKE_RE.sub(_repl, text)
    return text, count
