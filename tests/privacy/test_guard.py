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

"""PIIGuard S1 等价性测试：闸口行为与现有 PIIRedactor 逐字节一致。

S1 是行为保持的重构——guard.redact_structured / redact_for_cloud 必须等价于现状
PIIRedactor.redact_regex_only / redact_snippet，断言由「两者比较」给出，不写死占位符。
"""

from __future__ import annotations

from docrestore.pipeline.config import PIIConfig
from docrestore.privacy.guard import PIIGuard
from docrestore.privacy.redactor import EntityLexicon, PIIRedactor

# 含多类结构化 PII + 人名的样本（构造输入，断言从输入派生）
_SAMPLE = "负责人张三 13800138000 邮箱 dev@corp.example 卡 6222021234567890123"

# 合成高置信密钥 token（sk- + 30 位字母数字）与正文 KV 代码表达式（不该被改坏）
_PLANTED_SK = "sk-abcdefghijklmnopqrstuvwxyz0123"
_CODE_KV = "password = get_secret()"


def test_enabled_reflects_config() -> None:
    """enabled 透传 pii_cfg.enable。"""
    assert PIIGuard(PIIConfig(enable=True)).enabled is True
    assert PIIGuard(PIIConfig(enable=False)).enabled is False


def test_redact_structured_equiv_regex_only() -> None:
    """redact_structured("full") == PIIRedactor.redact_regex_only（逐字节）。"""
    cfg = PIIConfig(enable=True)
    guard = PIIGuard(cfg)
    expected, _ = PIIRedactor(cfg).redact_regex_only(_SAMPLE)
    assert guard.redact_structured(_SAMPLE) == expected


def test_redact_for_cloud_with_lexicon_equiv_snippet() -> None:
    """redact_for_cloud(text, lexicon) == PIIRedactor.redact_snippet（逐字节）。"""
    cfg = PIIConfig(enable=True, redact_person_name=True)
    guard = PIIGuard(cfg)
    lexicon = EntityLexicon(person_names=("张三",), org_names=())
    expected, _ = PIIRedactor(cfg).redact_snippet(_SAMPLE, lexicon)
    assert guard.redact_for_cloud(_SAMPLE, lexicon) == expected


def test_redact_for_cloud_no_lexicon_equiv_regex_only() -> None:
    """redact_for_cloud(text, None) 退化为仅结构化（== redact_regex_only）。"""
    cfg = PIIConfig(enable=True)
    guard = PIIGuard(cfg)
    expected, _ = PIIRedactor(cfg).redact_regex_only(_SAMPLE)
    assert guard.redact_for_cloud(_SAMPLE, None) == expected


def test_redact_idempotent() -> None:
    """对已脱敏文本再调一次，结果不变（幂等，下游可安全重复脱）。"""
    cfg = PIIConfig(enable=True)
    guard = PIIGuard(cfg)
    once = guard.redact_for_cloud(_SAMPLE, None)
    twice = guard.redact_for_cloud(once, None)
    assert once == twice


def test_tokens_only_redacts_high_confidence_token() -> None:
    """tokens_only 脱高置信密钥 token（sk-/AKIA/JWT）。"""
    cfg = PIIConfig(enable=True)
    guard = PIIGuard(cfg)
    out = guard.redact_structured(f"key = {_PLANTED_SK}", profile="tokens_only")
    assert _PLANTED_SK not in out
    assert cfg.credential_placeholder in out


def test_tokens_only_keeps_kv_and_structured_pii() -> None:
    """tokens_only 不跑 KV/手机/邮箱全量正则 → 正文代码与非 token PII 原样保留。"""
    cfg = PIIConfig(enable=True)
    guard = PIIGuard(cfg)
    out = guard.redact_structured(f"{_SAMPLE}\n{_CODE_KV}", profile="tokens_only")
    # 手机/邮箱（结构化 PII）未脱 —— 断言派生自输入
    assert "13800138000" in out
    assert "dev@corp.example" in out
    # KV 代码表达式不被改坏（核心：password = get_secret() 原样保留）
    assert _CODE_KV in out


def test_full_vs_tokens_only_differ_on_structured_pii() -> None:
    """同一输入：full 脱手机、tokens_only 不脱 —— 证明档位真实生效、非空转。"""
    guard = PIIGuard(PIIConfig(enable=True))
    full_out = guard.redact_structured(_SAMPLE, profile="full")
    tok_out = guard.redact_structured(_SAMPLE, profile="tokens_only")
    assert "13800138000" not in full_out  # full 脱手机
    assert "13800138000" in tok_out  # tokens_only 不脱
    assert full_out != tok_out


def test_redact_for_cloud_tokens_only_ignores_lexicon() -> None:
    """redact_for_cloud(tokens_only)：脱 token，但忽略 lexicon（不替实体保标识符）。"""
    cfg = PIIConfig(enable=True, redact_person_name=True)
    guard = PIIGuard(cfg)
    lexicon = EntityLexicon(person_names=("张三",), org_names=())
    out = guard.redact_for_cloud(
        f"张三 写了 key = {_PLANTED_SK}", lexicon, profile="tokens_only",
    )
    assert _PLANTED_SK not in out  # token 脱
    assert "张三" in out  # 实体未替（lexicon 忽略，保护标识符）
