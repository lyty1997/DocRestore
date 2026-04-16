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

"""Pipeline Profiler —— 任务全流程阶段耗时埋点

设计要点（详见 docs/zh/backend/performance_toolkit.md §5）：
- 默认关闭（PipelineConfig.profiling_enable=False）；禁用时 `stage()` 走
  `NullProfiler`，单次 ~50ns 开销可忽略
- 启用时事件收集到内存，任务结束调 `export_json()` 写 profile.json、
  `export_summary_table()` 打印扁平化耗时表
- 支持嵌套（stage 内再开 stage，自动维护 depth）
- 支持 worker 子进程回传的外部事件（`record_external`）统一汇总
- 线程安全：Pipeline 本身是 async 单事件循环，不需要锁；但 `record_external`
  可能由 gather 协程并发调用，用 `asyncio.Lock` 保护 events 列表
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class StageEvent:
    """一次 stage 的完整耗时记录。"""

    name: str
    start_ts: float  # time.monotonic() 基准
    duration_s: float
    depth: int
    attrs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Profiler(Protocol):
    """Profiler 统一接口，Pipeline 按 profiling_enable 实例化具体实现。"""

    def stage(
        self, name: str, **attrs: Any,
    ) -> contextlib.AbstractContextManager[None]:
        """打开一个 stage，返回 context manager；退出时记录耗时。"""
        ...

    def record_external(
        self, name: str, duration_s: float, **attrs: Any,
    ) -> None:
        """吸收外部（worker 子进程）事件，不参与嵌套栈。"""
        ...

    def export_json(self, path: Path) -> None:
        """把所有事件序列化为 JSON 数组写入 path。"""
        ...

    def export_summary_table(self) -> str:
        """返回扁平化耗时表文本（stdout 友好）。"""
        ...


# ─────────────────────────────────────────────────────────────────
# NullProfiler：禁用路径的零开销实现
# ─────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _null_ctx() -> Iterator[None]:
    yield


class NullProfiler:
    """禁用时的零开销实现：所有方法 no-op。

    `stage()` 每次返回同一个 context manager 对象，不触发任何计时 /
    对象分配。单次调用约 50ns（仅函数调用 + yield）。
    """

    __slots__ = ()

    def stage(
        self, name: str, **attrs: Any,
    ) -> contextlib.AbstractContextManager[None]:
        del name, attrs
        return _null_ctx()

    def record_external(
        self, name: str, duration_s: float, **attrs: Any,
    ) -> None:
        del name, duration_s, attrs
        return

    def export_json(self, path: Path) -> None:
        del path
        return

    def export_summary_table(self) -> str:
        return ""


# ─────────────────────────────────────────────────────────────────
# MemoryProfiler：启用路径的完整实现
# ─────────────────────────────────────────────────────────────────


class MemoryProfiler:
    """启用时的实现：事件收集到内存 list，任务结束统一导出。

    单次 stage() 调用约 1-2μs（time.monotonic + dict.copy + list.append）。
    预估一次任务产生几百条事件，总开销 < 1ms。
    """

    def __init__(self) -> None:
        self._events: list[StageEvent] = []
        self._depth_stack: list[str] = []
        # 线程锁：record_external 可能被 worker 回调跨线程调用
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def stage(
        self, name: str, **attrs: Any,
    ) -> Iterator[None]:
        """标准嵌套 stage —— 进入时入栈，退出时记录事件。"""
        start = time.monotonic()
        depth = len(self._depth_stack)
        self._depth_stack.append(name)
        try:
            yield
        finally:
            self._depth_stack.pop()
            duration = time.monotonic() - start
            with self._lock:
                self._events.append(StageEvent(
                    name=name,
                    start_ts=start,
                    duration_s=duration,
                    depth=depth,
                    attrs=dict(attrs),
                ))

    def record_external(
        self, name: str, duration_s: float, **attrs: Any,
    ) -> None:
        """外部事件：depth 统一为当前栈深度，不改变栈。"""
        depth = len(self._depth_stack)
        with self._lock:
            self._events.append(StageEvent(
                name=name,
                start_ts=time.monotonic(),  # 外部事件只关心 duration
                duration_s=duration_s,
                depth=depth,
                attrs=dict(attrs),
            ))

    def export_json(self, path: Path) -> None:
        """落盘完整事件流。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = [asdict(e) for e in self._events]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def export_summary_table(self) -> str:
        """聚合事件为 (name → count/total/mean/share) 表。

        share% 基准：name == "pipeline.total" 的总耗时（无此事件则用第一条事件）。
        表格按 total_s 降序，保持嵌套树状关系（depth 前缀 "  "）。
        """
        with self._lock:
            events = list(self._events)
        if not events:
            return ""

        # 基准总时长（用于 share%）
        total_anchor = next(
            (e.duration_s for e in events if e.name == "pipeline.total"),
            events[0].duration_s,
        )

        # 按 name 聚合
        grouped: dict[str, list[StageEvent]] = {}
        for e in events:
            grouped.setdefault(e.name, []).append(e)

        rows: list[tuple[str, int, float, float, float, int]] = []
        for name, evs in grouped.items():
            count = len(evs)
            total = sum(e.duration_s for e in evs)
            mean = total / count if count else 0.0
            share = (total / total_anchor * 100) if total_anchor > 0 else 0.0
            min_depth = min(e.depth for e in evs)
            rows.append((name, count, total, mean, share, min_depth))

        # 按 total 降序
        rows.sort(key=lambda r: -r[2])

        header = (
            f"{'stage':<36} {'count':>6} {'total_s':>10} "
            f"{'mean_s':>10} {'share%':>8}"
        )
        sep = "-" * len(header)
        lines = [header, sep]
        for name, count, total, mean, share, depth in rows:
            indent = "  " * depth
            lines.append(
                f"{indent + name:<36} {count:>6} {total:>10.2f} "
                f"{mean:>10.3f} {share:>7.1f}%"
            )
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# 工厂函数 + 环境变量覆盖
# ─────────────────────────────────────────────────────────────────


def create_profiler(*, enable: bool) -> Profiler:
    """按开关创建对应 Profiler 实例。

    环境变量 DOCRESTORE_PROFILING=1 可强制启用（便于临时调试不改配置）。
    """
    env_flag = os.environ.get("DOCRESTORE_PROFILING", "").strip()
    env_enable = env_flag in {"1", "true", "yes", "on"}
    if enable or env_enable:
        return MemoryProfiler()
    return NullProfiler()


# ─────────────────────────────────────────────────────────────────
# ContextVar：跨 await 传递当前任务的 profiler
# ─────────────────────────────────────────────────────────────────
# 用 contextvars 而非 Pipeline 实例属性：同一 Pipeline 可被多个任务
# 并发调用，实例属性会互相污染事件；ContextVar 自动与 asyncio Task 绑定。

_NULL_PROFILER_SINGLETON = NullProfiler()

_current_profiler: ContextVar[Profiler] = ContextVar(
    "docrestore_current_profiler",
    default=_NULL_PROFILER_SINGLETON,
)


def current_profiler() -> Profiler:
    """返回当前 asyncio 任务上下文中的 profiler。

    未设置时返回 NullProfiler 单例（零开销 no-op）。
    """
    return _current_profiler.get()


def set_current_profiler(profiler: Profiler) -> object:
    """绑定 profiler 到当前上下文，返回 token 供后续 reset 用。"""
    return _current_profiler.set(profiler)


def reset_current_profiler(token: object) -> None:
    """还原 profiler 绑定（对应 set 的 token）。"""
    _current_profiler.reset(token)  # type: ignore[arg-type]
