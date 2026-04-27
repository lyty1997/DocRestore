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

"""BaseLLMRefiner 与熔断器集成测试。

验证：
1. litellm.acompletion 连续失败时，熔断器被触发 open
2. open 后新的 refine 调用直接抛 LLMCircuitOpenError，不再命中 acompletion
3. 熔断器事件被 pipeline 订阅后能推进度帧
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docrestore.llm.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitState,
    LLMCircuitOpenError,
    get_breaker,
    reset_all_breakers,
)
from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.models import RefineContext
from docrestore.pipeline.config import LLMConfig


def _make_context() -> RefineContext:
    return RefineContext(
        segment_index=1, total_segments=1,
        overlap_before="", overlap_after="",
    )


def _make_response(content: str = "ok") -> SimpleNamespace:
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """每个测试开始前清空 breaker 注册表，避免跨测试污染。"""
    reset_all_breakers()


@pytest.mark.asyncio
async def test_consecutive_failures_trip_breaker_and_fail_fast() -> None:
    """真实路径：litellm 连续失败 → breaker 翻转 open → 后续调用 fail-fast。"""
    # 用紧阈值的 breaker 便于快速触发
    breaker = await get_breaker(
        "test-integration-model",
        config=CircuitBreakerConfig(
            min_failures=2,
            failure_rate_threshold=0.5,
            cool_down_seconds=5.0,
        ),
    )
    assert breaker.state == CircuitState.CLOSED

    call_count = 0

    async def failing_acompletion(**_: object) -> SimpleNamespace:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("provider down")

    config = LLMConfig(
        model="test-integration-model",
        api_key="k", max_retries=0,
    )
    refiner = CloudLLMRefiner(config)

    with patch(
        "docrestore.llm.base.litellm.acompletion",
        side_effect=failing_acompletion,
    ):
        # 第 1、2 次实际打到 litellm 并失败
        for _ in range(2):
            with pytest.raises(RuntimeError, match="provider down"):
                await refiner.refine("raw", _make_context())

    assert call_count == 2
    assert breaker.state == CircuitState.OPEN  # type: ignore[comparison-overlap]

    # 第 3 次应 fail-fast，不再触达 litellm
    with patch(
        "docrestore.llm.base.litellm.acompletion",
        side_effect=failing_acompletion,
    ):
        with pytest.raises(LLMCircuitOpenError) as exc:
            await refiner.refine("raw", _make_context())
        assert exc.value.model == "test-integration-model"
        assert call_count == 2  # litellm 未被再次调用


@pytest.mark.asyncio
async def test_success_keeps_breaker_closed() -> None:
    """成功路径不应让 breaker open。"""
    breaker = await get_breaker("happy-path-model")

    async def ok_acompletion(**_: object) -> SimpleNamespace:
        return _make_response("fine")

    config = LLMConfig(model="happy-path-model", api_key="k")
    refiner = CloudLLMRefiner(config)

    with patch(
        "docrestore.llm.base.litellm.acompletion",
        side_effect=ok_acompletion,
    ):
        for _ in range(5):
            result = await refiner.refine("raw", _make_context())
            assert result.markdown

    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_success_restores_closed() -> None:
    """冷却到期 → 半开探测成功 → 关闭熔断，后续调用恢复正常。"""
    breaker = await get_breaker(
        "recovery-model",
        config=CircuitBreakerConfig(
            min_failures=2,
            failure_rate_threshold=0.5,
            cool_down_seconds=0.1,  # 短冷却便于测试
        ),
    )

    fail_mode = True

    async def flipping_acompletion(**_: object) -> SimpleNamespace:
        if fail_mode:
            raise RuntimeError("transient")
        return _make_response("ok")

    config = LLMConfig(
        model="recovery-model", api_key="k", max_retries=0,
    )
    refiner = CloudLLMRefiner(config)

    with patch(
        "docrestore.llm.base.litellm.acompletion",
        side_effect=flipping_acompletion,
    ):
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await refiner.refine("raw", _make_context())
        assert breaker.state == CircuitState.OPEN

        # 等待冷却
        await asyncio.sleep(0.15)

        # 翻转成功模式，下一次探测应该恢复
        fail_mode = False
        result = await refiner.refine("raw", _make_context())
        assert result.markdown
        assert breaker.state == CircuitState.CLOSED  # type: ignore[comparison-overlap]


@pytest.mark.asyncio
async def test_open_event_triggers_listener() -> None:
    """OPEN 翻转时订阅的监听器应被调用。"""
    breaker = await get_breaker(
        "listener-model",
        config=CircuitBreakerConfig(
            min_failures=2, failure_rate_threshold=0.5,
            cool_down_seconds=10.0,
        ),
    )
    events: list[str] = []
    unsub = breaker.subscribe_open(lambda m, _t: events.append(m))

    try:
        async def failing(**_: object) -> SimpleNamespace:
            raise RuntimeError("boom")

        config = LLMConfig(
            model="listener-model", api_key="k", max_retries=0,
        )
        refiner = CloudLLMRefiner(config)
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=failing,
        ):
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    await refiner.refine("raw", _make_context())
    finally:
        unsub()

    assert events == ["listener-model"]
