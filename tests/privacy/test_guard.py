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

import pytest

from docrestore.pipeline.config import PIIConfig
from docrestore.privacy.guard import PIIGuard
from docrestore.privacy.redactor import EntityLexicon, PIIRedactor

# 含多类结构化 PII + 人名的样本（构造输入，断言从输入派生）
_SAMPLE = "负责人张三 13800138000 邮箱 dev@corp.example 卡 6222021234567890123"


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


def test_tokens_only_profile_not_yet_implemented() -> None:
    """S1 边界：tokens_only 档位留给 S2，显式 NotImplementedError。"""
    guard = PIIGuard(PIIConfig(enable=True))
    with pytest.raises(NotImplementedError):
        guard.redact_structured("x", profile="tokens_only")
    with pytest.raises(NotImplementedError):
        guard.redact_for_cloud("x", None, profile="tokens_only")
