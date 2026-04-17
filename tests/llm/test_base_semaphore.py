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

"""BaseLLMRefiner 全局 LLM 信号量限流测试。

验证注入的 asyncio.Semaphore 确实能约束并发进入 litellm.acompletion 的协程数。
覆盖两条路径：注入信号量 vs 未注入（默认放行）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.models import RefineContext
from docrestore.pipeline.config import LLMConfig


def _make_response(content: str = "ok") -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


def _make_context() -> RefineContext:
    return RefineContext(
        segment_index=1,
        total_segments=1,
        overlap_before="",
        overlap_after="",
    )


class TestSemaphoreRateLimit:
    """_call_llm 的并发上限行为。"""

    @pytest.mark.asyncio
    async def test_semaphore_caps_concurrent_calls(self) -> None:
        """并发刷 10 次 refine，同一时刻进入 acompletion 的不应超过 semaphore 上限。"""
        max_concurrent = 2
        sem = asyncio.Semaphore(max_concurrent)

        active = 0
        observed_peak = 0

        async def fake_acompletion(**_: object) -> SimpleNamespace:
            nonlocal active, observed_peak
            active += 1
            observed_peak = max(observed_peak, active)
            # 模拟真实 LLM 调用耗时，拉长窗口便于观察并发峰值
            await asyncio.sleep(0.02)
            active -= 1
            return _make_response("ok")

        config = LLMConfig(model="m", api_key="k")
        refiner = CloudLLMRefiner(config, semaphore=sem)

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            await asyncio.gather(
                *(refiner.refine("raw", _make_context()) for _ in range(10))
            )

        assert observed_peak <= max_concurrent
        assert active == 0

    @pytest.mark.asyncio
    async def test_no_semaphore_allows_unbounded(self) -> None:
        """未注入 semaphore 时不限流，多个 refine 可同时进入 acompletion。"""
        active = 0
        observed_peak = 0

        async def fake_acompletion(**_: object) -> SimpleNamespace:
            nonlocal active, observed_peak
            active += 1
            observed_peak = max(observed_peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return _make_response("ok")

        refiner = CloudLLMRefiner(LLMConfig(model="m", api_key="k"))

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            await asyncio.gather(
                *(refiner.refine("raw", _make_context()) for _ in range(6))
            )

        # 未注入信号量的路径允许所有协程同时进入
        assert observed_peak >= 3

    @pytest.mark.asyncio
    async def test_all_entry_points_go_through_semaphore(self) -> None:
        """所有 LLM 入口都走 semaphore，验证没有 bypass 路径。

        覆盖：refine / fill_gap / final_refine / detect_doc_boundaries
        / detect_pii_entities。
        """
        sem = asyncio.Semaphore(1)

        active = 0
        observed_peak = 0

        async def fake_acompletion(**_: object) -> SimpleNamespace:
            nonlocal active, observed_peak
            active += 1
            observed_peak = max(observed_peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            # 对所有入口都合法的响应：空 JSON 对象（PII 检测需要 dict），
            # refine/fill_gap/final_refine 按普通文本处理；
            # detect_doc_boundaries 解析 JSON 时非 list 会走 fallback（返回 []）。
            return _make_response("{}")

        from docrestore.models import Gap

        refiner = CloudLLMRefiner(
            LLMConfig(model="m", api_key="k"), semaphore=sem,
        )
        gap = Gap(
            after_image="b.jpg",
            context_before="before",
            context_after="after",
        )

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            await asyncio.gather(
                refiner.refine("raw", _make_context()),
                refiner.fill_gap(gap, "cur", "nxt", "b.jpg"),
                refiner.final_refine("md"),
                refiner.detect_doc_boundaries("md"),
                refiner.detect_pii_entities("t"),
            )

        assert observed_peak == 1
