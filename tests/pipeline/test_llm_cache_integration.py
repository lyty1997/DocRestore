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

"""Pipeline._refine_segment_with_cache 集成测试

覆盖 (result, used_refiner) 契约：
- miss → 调 refiner，成功则写缓存，used_refiner=True
- hit → 跳过 refiner 调用，used_refiner=False
- refiner 抛异常 → 回退原文，不写缓存，used_refiner=True（时延由 controller 采样）
- truncated=True 的 refiner 结果 → 不写缓存（与 LLMCache 协作）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from docrestore.llm.cache import LLMCache
from docrestore.models import Gap, RefinedResult
from docrestore.pipeline.config import LLMConfig
from docrestore.pipeline.pipeline import Pipeline


def _llm() -> LLMConfig:
    return LLMConfig(
        model="openai/glm-5",
        api_base="https://example.test/v1",
    )


@pytest.mark.asyncio
async def test_miss_calls_refiner_and_persists(tmp_path: Path) -> None:
    cache = LLMCache(tmp_path / ".llm_cache")
    refiner = AsyncMock()
    refiner.refine = AsyncMock(
        return_value=RefinedResult(markdown="refined out", gaps=[]),
    )

    result, used = await Pipeline._refine_segment_with_cache(
        refiner, "raw seg", 0, 1, cache, _llm(),
    )
    assert used is True
    assert result.markdown == "refined out"
    refiner.refine.assert_awaited_once()

    # 二次请求同文本 → 命中，不调 refiner
    refiner.refine.reset_mock()
    result2, used2 = await Pipeline._refine_segment_with_cache(
        refiner, "raw seg", 0, 1, cache, _llm(),
    )
    assert used2 is False
    assert result2.markdown == "refined out"
    refiner.refine.assert_not_awaited()


@pytest.mark.asyncio
async def test_refiner_exception_does_not_persist(
    tmp_path: Path,
) -> None:
    cache = LLMCache(tmp_path / ".llm_cache")
    refiner = AsyncMock()
    refiner.refine = AsyncMock(side_effect=RuntimeError("network down"))

    result, used = await Pipeline._refine_segment_with_cache(
        refiner, "raw seg", 0, 1, cache, _llm(),
    )
    # 异常时回退原文，used_refiner 仍 True（调用方据此把时延喂给 controller）
    assert used is True
    assert result.markdown == "raw seg"

    # 下次 resume 必须再次尝试（未写缓存）
    refiner.refine.reset_mock()
    refiner.refine = AsyncMock(
        return_value=RefinedResult(markdown="recovered"),
    )
    result2, used2 = await Pipeline._refine_segment_with_cache(
        refiner, "raw seg", 0, 1, cache, _llm(),
    )
    assert used2 is True
    assert result2.markdown == "recovered"


@pytest.mark.asyncio
async def test_truncated_not_cached(tmp_path: Path) -> None:
    cache = LLMCache(tmp_path / ".llm_cache")
    refiner = AsyncMock()
    refiner.refine = AsyncMock(
        return_value=RefinedResult(
            markdown="half", gaps=[], truncated=True,
        ),
    )

    result, _ = await Pipeline._refine_segment_with_cache(
        refiner, "long seg", 0, 1, cache, _llm(),
    )
    assert result.truncated is True
    # 截断结果不得写缓存：下次 resume 应重试
    refiner.refine.reset_mock()
    refiner.refine = AsyncMock(
        return_value=RefinedResult(markdown="full second try"),
    )
    result2, used2 = await Pipeline._refine_segment_with_cache(
        refiner, "long seg", 0, 1, cache, _llm(),
    )
    assert used2 is True
    assert result2.markdown == "full second try"


@pytest.mark.asyncio
async def test_refiner_none_returns_raw(tmp_path: Path) -> None:
    cache = LLMCache(tmp_path / ".llm_cache")
    result, used = await Pipeline._refine_segment_with_cache(
        None, "raw", 0, 1, cache, _llm(),
    )
    assert result.markdown == "raw"
    assert used is False


@pytest.mark.asyncio
async def test_disabled_cache_always_calls_refiner(
    tmp_path: Path,
) -> None:
    cache = LLMCache(tmp_path / ".llm_cache", enabled=False)
    refiner = AsyncMock()
    refiner.refine = AsyncMock(
        return_value=RefinedResult(markdown="ok"),
    )

    for _ in range(3):
        await Pipeline._refine_segment_with_cache(
            refiner, "same input", 0, 1, cache, _llm(),
        )
    assert refiner.refine.await_count == 3


@pytest.mark.asyncio
async def test_gaps_roundtrip_through_cache(tmp_path: Path) -> None:
    cache = LLMCache(tmp_path / ".llm_cache")
    refiner = AsyncMock()
    refiner.refine = AsyncMock(
        return_value=RefinedResult(
            markdown="done",
            gaps=[
                Gap(
                    after_image="p1.jpg",
                    context_before="hello",
                    context_after="world",
                ),
            ],
        ),
    )
    await Pipeline._refine_segment_with_cache(
        refiner, "raw", 0, 1, cache, _llm(),
    )

    result, _ = await Pipeline._refine_segment_with_cache(
        refiner, "raw", 0, 1, cache, _llm(),
    )
    assert len(result.gaps) == 1
    assert result.gaps[0].after_image == "p1.jpg"
    assert result.gaps[0].context_before == "hello"
