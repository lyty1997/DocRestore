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

"""LLMCircuitBreaker 状态转移单元测试。

用注入时钟让测试确定性：无 sleep，直接 bump clock。
"""

from __future__ import annotations

import pytest

from docrestore.llm.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitState,
    LLMCircuitBreaker,
    LLMCircuitOpenError,
    get_breaker,
    reset_all_breakers,
)


class _FakeClock:
    """可控时钟：tick(dt) 前进，now() 返回当前值。"""

    def __init__(self) -> None:
        self._now = 1000.0

    def now(self) -> float:
        return self._now

    def tick(self, dt: float) -> None:
        self._now += dt


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def breaker(clock: _FakeClock) -> LLMCircuitBreaker:
    config = CircuitBreakerConfig(
        window_seconds=30.0,
        min_failures=3,
        failure_rate_threshold=0.6,
        cool_down_seconds=10.0,
        cool_down_max_seconds=60.0,
    )
    return LLMCircuitBreaker("test-model", config, clock=clock.now)


class TestClosedState:
    """CLOSED 状态行为。"""

    async def test_initial_state_closed(
        self, breaker: LLMCircuitBreaker
    ) -> None:
        assert breaker.state is CircuitState.CLOSED
        await breaker.before_call()  # 不抛异常

    async def test_single_failure_stays_closed(
        self, breaker: LLMCircuitBreaker
    ) -> None:
        await breaker.on_failure()
        assert breaker.state is CircuitState.CLOSED

    async def test_below_min_failures_stays_closed(
        self, breaker: LLMCircuitBreaker
    ) -> None:
        """min_failures=3，只有 2 次失败不触发 open。"""
        await breaker.on_failure()
        await breaker.on_failure()
        assert breaker.state is CircuitState.CLOSED

    async def test_mixed_success_failure_below_rate(
        self,
        breaker: LLMCircuitBreaker,
    ) -> None:
        """交错出现失败和成功，失败率始终 < 0.6，应保持 CLOSED。

        注意：必须交错，因为连续 3 失败本身就会触发 open（100% 失败率）。
        这里模拟"偶尔失败、大多成功"的正常抖动场景。
        """
        # 1 失败 + 2 成功 + 1 失败 + 2 成功 + 1 失败 + 2 成功
        # 累计 3 失败 / 9 总数 = 33% < 60%
        for _ in range(3):
            await breaker.on_failure()
            await breaker.on_success()
            await breaker.on_success()
        assert breaker.state is CircuitState.CLOSED


class TestTripToOpen:
    """CLOSED → OPEN 翻转条件。"""

    async def test_three_failures_in_a_row_trips(
        self, breaker: LLMCircuitBreaker
    ) -> None:
        """连续 3 失败（min_failures=3, rate=100%）→ OPEN。"""
        for _ in range(3):
            await breaker.on_failure()
        assert breaker.state is CircuitState.OPEN

    async def test_failure_rate_threshold_trips(
        self, breaker: LLMCircuitBreaker
    ) -> None:
        """3 失败 + 1 成功，rate=3/4=0.75 ≥ 0.6 → OPEN。"""
        await breaker.on_success()
        await breaker.on_failure()
        await breaker.on_failure()
        await breaker.on_failure()
        assert breaker.state is CircuitState.OPEN

    async def test_fail_fast_when_open(
        self,
        breaker: LLMCircuitBreaker,
    ) -> None:
        for _ in range(3):
            await breaker.on_failure()
        assert breaker.state is CircuitState.OPEN
        with pytest.raises(LLMCircuitOpenError) as exc_info:
            await breaker.before_call()
        assert exc_info.value.model == "test-model"

    async def test_old_events_evicted_from_window(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        """超过 window_seconds 的旧失败不参与统计。"""
        for _ in range(2):
            await breaker.on_failure()
        clock.tick(35.0)  # 超过 window=30
        # 此前的 2 次失败应被踢出；新的 1 次失败不会触发 open
        await breaker.on_failure()
        assert breaker.state is CircuitState.CLOSED


class TestHalfOpen:
    """OPEN → HALF_OPEN → CLOSED / OPEN 转移。"""

    async def test_cool_down_not_expired_stays_open(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        for _ in range(3):
            await breaker.on_failure()
        clock.tick(5.0)  # cool_down=10，未到期
        with pytest.raises(LLMCircuitOpenError):
            await breaker.before_call()
        assert breaker.state is CircuitState.OPEN

    async def test_cool_down_expired_enters_half_open(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        for _ in range(3):
            await breaker.on_failure()
        clock.tick(11.0)  # 冷却到期
        await breaker.before_call()  # 放行探测
        assert breaker.state is CircuitState.HALF_OPEN

    async def test_half_open_blocks_concurrent_probes(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        """半开时只放行一个探测，并发调用应 fail-fast。"""
        for _ in range(3):
            await breaker.on_failure()
        clock.tick(11.0)
        await breaker.before_call()  # 第一个探测放行
        assert breaker.state is CircuitState.HALF_OPEN
        with pytest.raises(LLMCircuitOpenError):
            await breaker.before_call()  # 第二个应被拒

    async def test_probe_success_closes_circuit(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        for _ in range(3):
            await breaker.on_failure()
        clock.tick(11.0)
        await breaker.before_call()
        await breaker.on_success()
        assert breaker.state is CircuitState.CLOSED

    async def test_probe_failure_reopens_circuit(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        for _ in range(3):
            await breaker.on_failure()
        clock.tick(11.0)
        await breaker.before_call()
        await breaker.on_failure()
        assert breaker.state is CircuitState.OPEN

    async def test_cool_down_exponential_backoff(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        """连续重开冷却时长应翻倍，封顶到 cool_down_max。"""
        # 第 1 次 open，cool_down=10
        for _ in range(3):
            await breaker.on_failure()
        first_until = breaker.open_until
        clock.tick(11.0)
        await breaker.before_call()  # half_open
        await breaker.on_failure()   # 再次 open，cool_down 应翻倍到 20
        second_until = breaker.open_until
        # 第二次 open 的冷却时长应 ≈ 2× 第一次
        first_dt = first_until - 1000.0
        second_dt = second_until - clock.now()
        assert second_dt >= first_dt * 1.8  # 留点浮点误差空间
        assert second_dt <= 60.0  # 封顶


class TestListeners:
    """OPEN 事件监听器。"""

    async def test_listener_called_on_open(
        self,
        breaker: LLMCircuitBreaker,
    ) -> None:
        events: list[tuple[str, float]] = []
        breaker.subscribe_open(lambda m, t: events.append((m, t)))
        for _ in range(3):
            await breaker.on_failure()
        assert len(events) == 1
        assert events[0][0] == "test-model"

    async def test_listener_exception_does_not_break_breaker(
        self,
        breaker: LLMCircuitBreaker,
    ) -> None:
        def raising_listener(model: str, until: float) -> None:
            raise RuntimeError("boom")

        breaker.subscribe_open(raising_listener)
        for _ in range(3):
            await breaker.on_failure()
        assert breaker.state is CircuitState.OPEN  # 监听器异常不影响状态

    async def test_unsubscribe_stops_notifications(
        self,
        breaker: LLMCircuitBreaker,
        clock: _FakeClock,
    ) -> None:
        events: list[str] = []
        unsub = breaker.subscribe_open(
            lambda m, _: events.append(m),
        )
        for _ in range(3):
            await breaker.on_failure()
        assert len(events) == 1

        unsub()
        # 触发第二次 open
        clock.tick(11.0)
        await breaker.before_call()
        await breaker.on_failure()
        assert len(events) == 1  # 第二次不再通知


class TestRegistry:
    """get_breaker 单例注册表。"""

    async def test_same_model_returns_same_instance(self) -> None:
        reset_all_breakers()
        b1 = await get_breaker("m1")
        b2 = await get_breaker("m1")
        assert b1 is b2

    async def test_different_models_get_different_instances(self) -> None:
        reset_all_breakers()
        b1 = await get_breaker("m1")
        b2 = await get_breaker("m2")
        assert b1 is not b2

    async def test_reset_clears_registry(self) -> None:
        reset_all_breakers()
        b1 = await get_breaker("m1")
        reset_all_breakers()
        b2 = await get_breaker("m1")
        assert b1 is not b2
