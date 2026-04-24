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

"""LLM provider 熔断器

场景：provider 连续响应失败时，litellm 仍按 num_retries 盲目重试，多段并发下
浪费会被放大成 N × retries 倍。本模块在 _call_llm 外围套一层熔断器：

- **closed**（默认）：放行所有调用；每次失败/成功写入滑动窗口，窗口内失败率
  达阈值时翻转到 open。
- **open**：fail-fast，抛 LLMCircuitOpenError；到 cool_down 后首次调用
  自动翻转到 half_open。
- **half_open**：只放行一个探测调用；成功 → closed + 重置冷却；
  失败 → 重回 open，冷却时长指数退避到 cool_down_max。

每个 model 一个全局单例（`get_breaker(model)`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """熔断器三态"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """熔断器参数"""

    #: 滑动窗口时长，超过此时间的事件从窗口中剔除。
    window_seconds: float = 30.0
    #: 至少累积 N 次失败才考虑 open，避免单点抖动触发。
    min_failures: int = 3
    #: 窗口内失败率 ≥ 此值 + 达到 min_failures 时 open。
    failure_rate_threshold: float = 0.6
    #: 首次 open 的冷却时长（秒）。
    cool_down_seconds: float = 60.0
    #: 冷却上限（秒）—— 连续重开后指数退避到此值封顶。
    cool_down_max_seconds: float = 600.0


class LLMCircuitOpenError(RuntimeError):
    """熔断器处于 open 状态，调用被拒绝（快速失败）。

    调用方已有的 `except Exception` fallback 会捕获此异常。想精确处理
    的调用方可以 `except LLMCircuitOpenError` 做差异化日志/提示。
    """

    def __init__(self, model: str, open_until: float) -> None:
        self.model = model
        self.open_until = open_until
        remain = max(0.0, open_until - time.monotonic())
        super().__init__(
            f"LLM circuit breaker open for model={model}, "
            f"retry in {remain:.0f}s",
        )


OpenListener = Callable[[str, float], None]
"""回调签名：(model, open_until) → None。在 OPEN 转换时同步调用。"""


class LLMCircuitBreaker:
    """per-model 熔断器。"""

    def __init__(
        self,
        model: str,
        config: CircuitBreakerConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._model = model
        self._config = config or CircuitBreakerConfig()
        self._clock = clock or time.monotonic
        self._state: CircuitState = CircuitState.CLOSED
        self._events: deque[tuple[float, bool]] = deque()  # (ts, is_failure)
        self._open_until: float = 0.0
        self._cool_down: float = self._config.cool_down_seconds
        self._probe_in_flight: bool = False
        self._listeners: list[OpenListener] = []
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """当前状态快照，不加锁（仅诊断用）。"""
        return self._state

    @property
    def model(self) -> str:
        return self._model

    @property
    def open_until(self) -> float:
        """OPEN/HALF_OPEN 情况下的冷却结束时间（基于 clock）。CLOSED 时为 0。"""
        return self._open_until

    def subscribe_open(self, fn: OpenListener) -> Callable[[], None]:
        """注册 OPEN 事件监听器，返回取消订阅的句柄。

        监听器在 `_trip()` 内同步调用；异常会被吞并记录，不影响熔断状态。
        调用方离开作用域时应调用返回的 unsubscribe() 防止泄漏。
        """
        self._listeners.append(fn)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(fn)

        return unsubscribe

    async def before_call(self) -> None:
        """调用前校验。

        - CLOSED：直接放行
        - OPEN：若冷却到期，翻转 HALF_OPEN 并放行一个探测；否则 fail-fast
        - HALF_OPEN：已有探测在途则 fail-fast；否则放行并标记 in_flight
        """
        async with self._lock:
            now = self._clock()
            if self._state == CircuitState.CLOSED:
                return
            if self._state == CircuitState.OPEN:
                if now < self._open_until:
                    raise LLMCircuitOpenError(self._model, self._open_until)
                # 冷却到期 → 进入半开
                self._state = CircuitState.HALF_OPEN
                self._probe_in_flight = True
                logger.info(
                    "LLM circuit breaker → HALF_OPEN: model=%s",
                    self._model,
                )
                return
            # HALF_OPEN
            if self._probe_in_flight:
                raise LLMCircuitOpenError(self._model, self._open_until)
            self._probe_in_flight = True

    async def on_success(self) -> None:
        """调用成功回调。"""
        async with self._lock:
            now = self._clock()
            self._events.append((now, False))
            self._evict_old(now)
            if self._state == CircuitState.HALF_OPEN:
                # 探测成功：关闭熔断，重置冷却
                self._state = CircuitState.CLOSED
                self._probe_in_flight = False
                self._cool_down = self._config.cool_down_seconds
                self._open_until = 0.0
                self._events.clear()
                logger.info(
                    "LLM circuit breaker → CLOSED: model=%s",
                    self._model,
                )

    async def on_failure(self) -> None:
        """调用失败回调。"""
        async with self._lock:
            now = self._clock()
            self._events.append((now, True))
            self._evict_old(now)
            if self._state == CircuitState.HALF_OPEN:
                # 探测失败 → 重回 OPEN，指数退避冷却
                self._probe_in_flight = False
                self._trip(now)
                return
            if self._state == CircuitState.CLOSED:
                total = len(self._events)
                failures = sum(1 for _, f in self._events if f)
                if (
                    failures >= self._config.min_failures
                    and total > 0
                    and failures / total >= self._config.failure_rate_threshold
                ):
                    self._trip(now)

    def _evict_old(self, now: float) -> None:
        cutoff = now - self._config.window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def _trip(self, now: float) -> None:
        """翻转到 OPEN，触发监听器。"""
        self._state = CircuitState.OPEN
        self._open_until = now + self._cool_down
        logger.warning(
            "LLM circuit breaker OPEN: model=%s cool_down=%.0fs",
            self._model, self._cool_down,
        )
        for fn in list(self._listeners):
            try:
                fn(self._model, self._open_until)
            except Exception:
                logger.exception(
                    "LLM circuit breaker listener 异常: model=%s",
                    self._model,
                )
        # 指数退避到 cool_down_max
        self._cool_down = min(
            self._config.cool_down_max_seconds,
            self._cool_down * 2,
        )


# 模块级注册表：per-model 单例
_breakers: dict[str, LLMCircuitBreaker] = {}
_registry_lock = asyncio.Lock()


async def get_breaker(
    model: str,
    config: CircuitBreakerConfig | None = None,
) -> LLMCircuitBreaker:
    """获取或创建 per-model 熔断器单例。

    config 仅在首次创建时生效；后续调用忽略传入的 config。
    """
    async with _registry_lock:
        breaker = _breakers.get(model)
        if breaker is None:
            breaker = LLMCircuitBreaker(model, config)
            _breakers[model] = breaker
        return breaker


def reset_all_breakers() -> None:
    """清空所有 breaker。仅供测试和进程重启用。"""
    _breakers.clear()
