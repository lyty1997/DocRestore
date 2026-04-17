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

"""PipelineScheduler 单元测试"""

from __future__ import annotations

import asyncio
import time

import pytest

from docrestore.pipeline.scheduler import PipelineScheduler


class TestGPULock:
    """GPU Lock 互斥测试"""

    @pytest.mark.asyncio
    async def test_gpu_lock_serializes(self) -> None:
        """GPU lock 确保同一时刻只有一个协程持有"""
        scheduler = PipelineScheduler()
        lock = scheduler.gpu_lock

        order: list[str] = []

        async def worker(name: str, delay: float) -> None:
            async with lock:
                order.append(f"{name}_start")
                await asyncio.sleep(delay)
                order.append(f"{name}_end")

        await asyncio.gather(
            worker("a", 0.05),
            worker("b", 0.05),
        )

        # a 和 b 串行执行，不会交叉
        assert order[0] == "a_start"
        assert order[1] == "a_end"
        assert order[2] == "b_start"
        assert order[3] == "b_end"

    @pytest.mark.asyncio
    async def test_lock_is_reentrant_across_calls(self) -> None:
        """同一协程可多次获取锁（不同 async with 调用）"""
        scheduler = PipelineScheduler()
        lock = scheduler.gpu_lock

        async with lock:
            pass
        # 第二次获取不应死锁
        async with lock:
            pass


class TestLLMSemaphore:
    """LLM API 全局并发限流测试"""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self) -> None:
        """semaphore 限制同时运行的协程数"""
        max_concurrent = 2
        scheduler = PipelineScheduler(
            max_concurrent_llm_requests=max_concurrent
        )
        sem = scheduler.llm_semaphore

        concurrent_count = 0
        max_observed = 0

        async def worker() -> None:
            nonlocal concurrent_count, max_observed
            async with sem:
                concurrent_count += 1
                max_observed = max(max_observed, concurrent_count)
                await asyncio.sleep(0.05)
                concurrent_count -= 1

        await asyncio.gather(
            *(worker() for _ in range(5))
        )

        assert max_observed <= max_concurrent
        assert concurrent_count == 0

    @pytest.mark.asyncio
    async def test_semaphore_default_value(self) -> None:
        """默认并发数为 3"""
        scheduler = PipelineScheduler()
        sem = scheduler.llm_semaphore

        concurrent_count = 0
        max_observed = 0

        async def worker() -> None:
            nonlocal concurrent_count, max_observed
            async with sem:
                concurrent_count += 1
                max_observed = max(max_observed, concurrent_count)
                await asyncio.sleep(0.03)
                concurrent_count -= 1

        await asyncio.gather(
            *(worker() for _ in range(6))
        )

        assert max_observed <= 3

    @pytest.mark.asyncio
    async def test_semaphore_throughput(self) -> None:
        """并发执行比串行快"""
        scheduler = PipelineScheduler(max_concurrent_llm_requests=3)
        sem = scheduler.llm_semaphore

        async def worker() -> None:
            async with sem:
                await asyncio.sleep(0.05)

        start = time.monotonic()
        await asyncio.gather(*(worker() for _ in range(3)))
        elapsed = time.monotonic() - start

        # 3 个 0.05s 任务并发应在 ~0.05s 完成，远小于串行 0.15s
        assert elapsed < 0.12


class TestSchedulerIntegration:
    """GPU Lock + Semaphore 组合测试"""

    @pytest.mark.asyncio
    async def test_lock_and_semaphore_independent(self) -> None:
        """gpu_lock 和 llm_semaphore 互不影响"""
        scheduler = PipelineScheduler(max_concurrent_llm_requests=2)

        results: list[str] = []

        async def ocr_worker() -> None:
            """模拟 OCR（需要 GPU Lock）"""
            async with scheduler.gpu_lock:
                results.append("ocr")
                await asyncio.sleep(0.01)

        async def llm_worker(name: str) -> None:
            """模拟 LLM 调用（需要 Semaphore）"""
            async with scheduler.llm_semaphore:
                results.append(f"llm_{name}")
                await asyncio.sleep(0.01)

        # OCR 和 LLM 可以同时运行（不同锁）
        await asyncio.gather(
            ocr_worker(),
            llm_worker("a"),
            llm_worker("b"),
        )

        assert "ocr" in results
        assert "llm_a" in results
        assert "llm_b" in results
