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

"""LLM 凭据环境回填单测（#37）。

落库不再存明文 api_key，水合 / 重启时由 ``refill_api_key_from_env`` 从环境补回。
覆盖：空 key 才回填、显式 key 绝不被环境覆盖、无环境 / 空白环境原样返回、
回填不原地修改入参。
"""

from __future__ import annotations

import pytest

from docrestore.llm.credentials import (
    ENV_LLM_API_KEY,
    refill_api_key_from_env,
)
from docrestore.pipeline.config import LLMConfig


def test_refills_when_key_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_key 为空且环境有值 → 回填环境 key，其余字段保留。"""
    monkeypatch.setenv(ENV_LLM_API_KEY, "sk-from-env")
    llm = LLMConfig(model="gpt-4", api_key="")
    result = refill_api_key_from_env(llm)
    assert result.api_key == "sk-from-env"
    assert result.model == "gpt-4"
    # model_copy 返回新对象，不原地改入参
    assert llm.api_key == ""


def test_keeps_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """已显式带 key → 环境绝不覆盖，原对象原样返回。"""
    monkeypatch.setenv(ENV_LLM_API_KEY, "sk-from-env")
    llm = LLMConfig(model="gpt-4", api_key="sk-explicit")
    result = refill_api_key_from_env(llm)
    assert result.api_key == "sk-explicit"
    assert result is llm


def test_no_env_returns_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境未设 → 原样返回（api_key 保持空）。"""
    monkeypatch.delenv(ENV_LLM_API_KEY, raising=False)
    llm = LLMConfig(model="gpt-4", api_key="")
    result = refill_api_key_from_env(llm)
    assert result.api_key == ""
    assert result is llm


def test_blank_env_returns_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境为纯空白 → 视同未设，原样返回。"""
    monkeypatch.setenv(ENV_LLM_API_KEY, "   ")
    llm = LLMConfig(model="gpt-4", api_key="")
    result = refill_api_key_from_env(llm)
    assert result.api_key == ""
    assert result is llm
