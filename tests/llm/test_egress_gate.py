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

"""出云闸口（#67）测试：fail-closed 拒发（堵 N1）+ 仅实体兜底（堵 N2）。

断言一律从输入派生（构造的人名/机构名），不写死数据集标识符。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.egress_gate import (
    CloudEgressBlockedError,
    CloudEgressPolicy,
    current_egress_policy,
    egress_scope,
    enforce_egress,
)
from docrestore.llm.local import LocalLLMRefiner
from docrestore.models import Gap, RefineContext
from docrestore.pipeline.config import LLMConfig, PIIConfig
from docrestore.privacy.guard import PIIGuard
from docrestore.privacy.redactor import EntityLexicon

# 测试用人名/机构名（断言从此派生：脱敏后这些原串不得出现，占位符须出现）
_PERSON = "张三"
_ORG = "示例机构"


def _make_response(content: str = "ok") -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


def _make_context() -> RefineContext:
    return RefineContext(
        segment_index=1, total_segments=1,
        overlap_before="", overlap_after="",
    )


def _make_gap() -> Gap:
    return Gap(
        after_image="b.jpg", context_before="before", context_after="after",
    )


def _pii_cfg() -> PIIConfig:
    return PIIConfig(
        enable=True, redact_person_name=True, redact_org_name=True,
    )


def _lexicon() -> EntityLexicon:
    return EntityLexicon(person_names=(_PERSON,), org_names=(_ORG,))


def _policy(*, block_cloud: bool, with_pii: bool = True) -> CloudEgressPolicy:
    cfg = _pii_cfg()
    return CloudEgressPolicy(
        block_cloud=block_cloud,
        lexicon=_lexicon() if with_pii else None,
        guard=PIIGuard(cfg) if with_pii else None,
    )


class TestEnforceEgressUnit:
    """enforce_egress 纯函数行为（不经 litellm）。"""

    def test_block_cloud_cloud_provider_raises(self) -> None:
        """cloud provider + block_cloud → 抛 CloudEgressBlockedError。"""
        with egress_scope(_policy(block_cloud=True)):
            with pytest.raises(CloudEgressBlockedError):
                enforce_egress({"messages": []}, "cloud")

    def test_block_cloud_local_provider_passes(self) -> None:
        """local provider 即便 block_cloud=True 也放行（本地不出云）。"""
        with egress_scope(_policy(block_cloud=True)):
            enforce_egress({"messages": []}, "local")  # 不抛即通过

    def test_no_policy_passthrough(self) -> None:
        """未安装策略 → 放行且不改 kwargs（向后兼容测试/直构）。"""
        kwargs: dict[str, Any] = {
            "messages": [{"role": "user", "content": f"作者 {_PERSON}"}],
        }
        enforce_egress(kwargs, "cloud")
        assert kwargs["messages"][0]["content"] == f"作者 {_PERSON}"

    def test_entity_redaction_on_user_messages(self) -> None:
        """非 system 消息的人名/机构名被替换为占位符。"""
        kwargs: dict[str, Any] = {
            "messages": [
                {"role": "user", "content": f"作者 {_PERSON} 来自 {_ORG}"},
            ],
        }
        with egress_scope(_policy(block_cloud=False)):
            enforce_egress(kwargs, "cloud")
        out = kwargs["messages"][0]["content"]
        assert _PERSON not in out
        assert _ORG not in out

    def test_system_message_not_redacted(self) -> None:
        """system 指令文本不被脱敏（避免污染 LLM 指令）。"""
        sys_text = f"示例占位 {_PERSON}（请勿改写本指令）"
        kwargs: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": sys_text},
                {"role": "user", "content": f"正文 {_PERSON}"},
            ],
        }
        with egress_scope(_policy(block_cloud=False)):
            enforce_egress(kwargs, "cloud")
        assert kwargs["messages"][0]["content"] == sys_text  # system 原样
        assert _PERSON not in kwargs["messages"][1]["content"]  # user 被脱

    def test_prediction_content_redacted(self) -> None:
        """prediction.content（同源出云第二载荷）一并实体脱敏。"""
        kwargs: dict[str, Any] = {
            "messages": [{"role": "user", "content": "x"}],
            "prediction": {"type": "content", "content": f"原文 {_PERSON}"},
        }
        with egress_scope(_policy(block_cloud=False)):
            enforce_egress(kwargs, "cloud")
        assert _PERSON not in kwargs["prediction"]["content"]

    def test_guard_none_no_redaction(self) -> None:
        """未开 PII（guard/lexicon 为 None）→ 不脱敏。"""
        kwargs: dict[str, Any] = {
            "messages": [{"role": "user", "content": f"作者 {_PERSON}"}],
        }
        with egress_scope(_policy(block_cloud=False, with_pii=False)):
            enforce_egress(kwargs, "cloud")
        assert kwargs["messages"][0]["content"] == f"作者 {_PERSON}"

    def test_idempotent(self) -> None:
        """对已脱敏 kwargs 再过闸口，结果不变（占位符不被二次匹配）。"""
        kwargs: dict[str, Any] = {
            "messages": [{"role": "user", "content": f"作者 {_PERSON}"}],
        }
        with egress_scope(_policy(block_cloud=False)):
            enforce_egress(kwargs, "cloud")
            once = kwargs["messages"][0]["content"]
            enforce_egress(kwargs, "cloud")
        assert kwargs["messages"][0]["content"] == once


def _cloud_refiner() -> CloudLLMRefiner:
    return CloudLLMRefiner(LLMConfig(model="m", api_key="k"))


def _local_refiner() -> LocalLLMRefiner:
    return LocalLLMRefiner(
        LLMConfig(
            model="m", provider="local",
            api_base="http://localhost:11434/v1", api_key="k",
        ),
    )


class TestCallLlmGate:
    """经 BaseLLMRefiner._call_llm 的闸口（mock litellm.acompletion）。"""

    @pytest.mark.asyncio
    async def test_block_cloud_blocks_all_entry_points(self) -> None:
        """fail-closed 时 refine/fill_gap/final_refine 全部拒发，litellm 0 次调用。

        这是 N1 类绕过的结构兜底：任何出云路径必经 _call_llm，block_cloud=True
        时一律抛 CloudEgressBlockedError，整篇/段内容一字不出云。
        """
        calls = 0

        async def fake_acompletion(**_: Any) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return _make_response("ok")

        refiner = _cloud_refiner()
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ), egress_scope(_policy(block_cloud=True)):
            with pytest.raises(CloudEgressBlockedError):
                await refiner.refine(f"段 {_PERSON}", _make_context())
            with pytest.raises(CloudEgressBlockedError):
                await refiner.fill_gap(_make_gap(), "cur", "nxt", "b.jpg")
            with pytest.raises(CloudEgressBlockedError):
                await refiner.final_refine(f"整篇 {_PERSON}")
        assert calls == 0

    @pytest.mark.asyncio
    async def test_gate_redacts_entities_before_cloud(self) -> None:
        """非 block 时，送达 litellm 的 messages/prediction 已实体脱敏（堵 N2）。"""
        captured: dict[str, Any] = {}

        async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            return _make_response("ok")

        # 开 prediction：final_refine 把 markdown 作为预测载荷，验证闸口连同
        # prediction.content（同源出云第二载荷）一并脱敏，不留 fail-open。
        refiner = CloudLLMRefiner(
            LLMConfig(model="m", api_key="k", enable_prediction=True),
        )
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ), egress_scope(_policy(block_cloud=False)):
            await refiner.final_refine(f"作者 {_PERSON} 来自 {_ORG}")

        # 非 system 消息正文不得含原始人名/机构名
        for msg in captured["messages"]:
            if msg.get("role") == "system":
                continue
            assert _PERSON not in msg["content"]
            assert _ORG not in msg["content"]
        # prediction.content（final_refine 把 markdown 作为预测载荷）同样脱敏
        assert _PERSON not in captured["prediction"]["content"]

    @pytest.mark.asyncio
    async def test_local_provider_not_redacted_not_blocked(self) -> None:
        """provider=local：闸口短路——既不拒发也不脱敏（本地数据原样不出本机）。"""
        captured: dict[str, Any] = {}

        async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            return _make_response("ok")

        refiner = _local_refiner()
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ), egress_scope(_policy(block_cloud=True)):
            # block_cloud=True 但 local → 不抛，正常调用
            await refiner.final_refine(f"本地 {_PERSON}")

        joined = " ".join(
            str(m.get("content", "")) for m in captured["messages"]
        )
        assert _PERSON in joined  # 本地未脱敏，原文人名仍在


class TestConcurrencyIsolation:
    """ContextVar task-local：并发子目录的出云策略互不串味。"""

    @pytest.mark.asyncio
    async def test_two_tasks_independent_policies(self) -> None:
        """两协程各持不同 block_cloud，并发交错后互不影响。

        block 协程应拒发（不进 litellm），非 block 协程应成功且其内容被实体脱敏。
        若 ContextVar 串味，第二个 set 会覆盖第一个 → 行为错乱。
        """
        contents: list[str] = []

        async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
            msgs = kwargs.get("messages", [])
            contents.append(
                " ".join(str(m.get("content", "")) for m in msgs)
            )
            await asyncio.sleep(0.01)
            return _make_response("ok")

        refiner = _cloud_refiner()

        async def worker(*, block: bool, tag: str) -> str:
            with egress_scope(_policy(block_cloud=block)):
                # 让出事件循环，确保两协程的策略同时"在场"，最大化串味暴露
                await asyncio.sleep(0.01)
                assert current_egress_policy() is not None
                await refiner.final_refine(f"{tag} {_PERSON}")
                return tag

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            results = await asyncio.gather(
                worker(block=True, tag="BLOCK"),
                worker(block=False, tag="PASS"),
                return_exceptions=True,
            )

        blocked, passed = results
        assert isinstance(blocked, CloudEgressBlockedError)
        assert passed == "PASS"
        # 只有非 block 协程进了 litellm，且其内容被实体脱敏
        assert len(contents) == 1
        assert _PERSON not in contents[0]
