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

"""BaseLLMRefiner 自适应 timeout 测试。

背景：gpt-5.4-nano profile 出现过 904s 单次 api_call 挂起 —— 那是旧
LLMConfig.timeout=600s + retry 凑出来的。改成"按 input 线性动态" +
timeout_max_s 上限后，超长挂起会在 180s 内被切断，litellm 的 num_retries
负责补偿瞬时失败。

本组测试覆盖：
- 短 input → base timeout
- 长 input → base + per_1k * chars
- 超长 input → timeout_max_s 封顶
- 透传到 litellm kwargs
- num_retries 仍走 max_retries
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
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


class TestComputeTimeout:
    """_compute_timeout 纯函数行为：base + per_1k × chars，clamp 到 max。"""

    def test_short_input_uses_base(self) -> None:
        """input 极短时直接返回 base timeout。"""
        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=60,
            timeout_per_1k_chars_s=3.0,
            timeout_max_s=180,
        )
        refiner = CloudLLMRefiner(cfg)
        # 100 字符 * 3/1000 = 0.3s，远小于 base，应返回 60
        messages = [{"role": "user", "content": "a" * 100}]
        assert refiner._compute_timeout(messages) == 60

    def test_long_input_scales_linearly(self) -> None:
        """中等 input：base + per_1k × (chars/1000)。"""
        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=60,
            timeout_per_1k_chars_s=3.0,
            timeout_max_s=180,
        )
        refiner = CloudLLMRefiner(cfg)
        # 10_000 字符 → 60 + 3 × 10 = 90s
        messages = [{"role": "user", "content": "a" * 10_000}]
        assert refiner._compute_timeout(messages) == 90

    def test_huge_input_clamped_to_max(self) -> None:
        """超长 input → 封顶到 timeout_max_s，不是无限放大。"""
        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=60,
            timeout_per_1k_chars_s=3.0,
            timeout_max_s=180,
        )
        refiner = CloudLLMRefiner(cfg)
        # 200_000 字符 → 60 + 3 × 200 = 660s，应 clamp 到 180
        messages = [{"role": "user", "content": "a" * 200_000}]
        assert refiner._compute_timeout(messages) == 180

    def test_multi_message_sums_chars(self) -> None:
        """多条 messages 的 content 长度应累加。"""
        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=60,
            timeout_per_1k_chars_s=3.0,
            timeout_max_s=180,
        )
        refiner = CloudLLMRefiner(cfg)
        # system 5_000 + user 5_000 = 10_000 → 90s
        messages = [
            {"role": "system", "content": "s" * 5_000},
            {"role": "user", "content": "u" * 5_000},
        ]
        assert refiner._compute_timeout(messages) == 90


class TestTimeoutPassedToLitellm:
    """动态 timeout 正确注入 litellm.acompletion kwargs。"""

    @pytest.mark.asyncio
    async def test_small_segment_passes_base_timeout(self) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return _make_response()

        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=60,
            timeout_per_1k_chars_s=3.0,
            timeout_max_s=180,
            max_retries=2,
        )
        refiner = CloudLLMRefiner(cfg)

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            await refiner.refine("短段", _make_context())

        assert captured["num_retries"] == 2
        # 短 user 输入，但 refine system prompt 本身也占字符，累计约 2k-3k，
        # 最终 timeout 应接近 base（60~70s 之间）、远低于 max
        t = cast(int, captured["timeout"])
        assert 60 <= t <= 80

    @pytest.mark.asyncio
    async def test_large_segment_gets_scaled_timeout(self) -> None:
        """大 input 走线性放大；不应超过 timeout_max_s。"""
        captured: list[int] = []

        async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
            captured.append(cast(int, kwargs["timeout"]))
            return _make_response()

        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=60,
            timeout_per_1k_chars_s=3.0,
            timeout_max_s=180,
        )
        refiner = CloudLLMRefiner(cfg)

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            await refiner.refine("x" * 30_000, _make_context())

        assert len(captured) == 1
        t = captured[0]
        # 实际 timeout 必须在 base 与 max 之间，且随 input 变大
        assert 60 < t <= 180

    @pytest.mark.asyncio
    async def test_timeout_triggers_retry(self) -> None:
        """litellm 层面 timeout 时，BaseLLMRefiner 通过 num_retries 自动补偿。

        用 fake_acompletion 先抛 TimeoutError 再成功，模拟一次重试命中。
        验证 max_retries 仍然透传给 litellm（实际重试由 litellm 实现）。
        """
        captured: dict[str, object] = {}
        call_count = 0

        async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
            nonlocal call_count
            captured.update(kwargs)
            call_count += 1
            if call_count == 1:
                # 第一次超时（模拟）
                raise TimeoutError("upstream slow")
            return _make_response("ok")

        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=60, max_retries=3,
        )
        refiner = CloudLLMRefiner(cfg)

        # 注意：litellm 自己的 retry 逻辑在 patch 下不会触发。
        # 这个用例验证的是 num_retries=3 被传入 kwargs —— 真实 retry 发生在
        # litellm 内部，靠 vendor 代码。这里只保证参数穿透不丢。
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            with pytest.raises(TimeoutError):
                await refiner.refine("raw", _make_context())

        assert captured["num_retries"] == 3
        assert call_count == 1  # 这里只确认被调一次（patch 下不 retry）

    @pytest.mark.asyncio
    async def test_no_regression_from_zero_per_1k(self) -> None:
        """timeout_per_1k_chars_s=0 等价于固定 timeout（保持向后兼容）。"""
        captured: list[int] = []

        async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
            captured.append(cast(int, kwargs["timeout"]))
            return _make_response()

        cfg = LLMConfig(
            model="m", api_key="k",
            timeout=120,
            timeout_per_1k_chars_s=0.0,  # 关闭动态放大
            timeout_max_s=300,
        )
        refiner = CloudLLMRefiner(cfg)

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            # 随意长度，动态项归零 → 始终 120
            await refiner.refine("x" * 50_000, _make_context())

        assert captured == [120]
