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

"""Pipeline + LocalLLMRefiner 集成测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.local import LocalLLMRefiner
from docrestore.models import RefineContext, RefinedResult
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline


class TestCreateRefiner:
    """_create_refiner 根据 provider 创建正确的 refiner"""

    def test_cloud_provider(self) -> None:
        """provider='cloud' 创建 CloudLLMRefiner"""
        cfg = LLMConfig(
            provider="cloud",
            model="openai/gpt-4",
        )
        refiner = Pipeline(PipelineConfig())._create_refiner(cfg)
        assert isinstance(refiner, CloudLLMRefiner)

    def test_local_provider(self) -> None:
        """provider='local' 创建 LocalLLMRefiner"""
        cfg = LLMConfig(
            provider="local",
            model="ollama/qwen2.5",
            api_base="http://localhost:11434/v1",
        )
        refiner = Pipeline(PipelineConfig())._create_refiner(cfg)
        assert isinstance(refiner, LocalLLMRefiner)

    def test_default_provider_is_cloud(self) -> None:
        """默认 provider 为 'cloud'"""
        cfg = LLMConfig(model="openai/gpt-4")
        assert cfg.provider == "cloud"
        refiner = Pipeline(PipelineConfig())._create_refiner(cfg)
        assert isinstance(refiner, CloudLLMRefiner)


class TestPipelineInitializeLocal:
    """Pipeline.initialize() 使用 local provider"""

    @pytest.mark.asyncio
    async def test_initialize_creates_local_refiner(self) -> None:
        """provider='local' 时 initialize 创建 LocalLLMRefiner"""
        from unittest.mock import AsyncMock, MagicMock

        config = PipelineConfig(
            llm=LLMConfig(
                provider="local",
                model="ollama/qwen2.5",
                api_base="http://localhost:11434/v1",
            ),
        )
        pipeline = Pipeline(config)

        # 注入 mock OCR 引擎避免初始化失败
        mock_ocr = MagicMock()
        mock_ocr.initialize = AsyncMock()
        pipeline.set_ocr_engine(mock_ocr)

        await pipeline.initialize()
        assert isinstance(pipeline._refiner, LocalLLMRefiner)


class TestLocalRefinerPII:
    """local provider + PII 场景"""

    def test_local_refiner_not_cloud_instance(self) -> None:
        """LocalLLMRefiner 不通过 isinstance(CloudLLMRefiner) 检查"""
        cfg = LLMConfig(
            provider="local",
            model="ollama/qwen2.5",
        )
        refiner = Pipeline(PipelineConfig())._create_refiner(cfg)
        # _redact_pii 中的 isinstance 检查
        assert not isinstance(refiner, CloudLLMRefiner)


class TestLocalRefinerSegments:
    """local provider + mock refiner 精修流程"""

    @pytest.mark.asyncio
    async def test_refine_one_segment_with_local(self) -> None:
        """使用 mock LocalLLMRefiner 精修单段"""
        mock_refiner = MagicMock(spec=LocalLLMRefiner)
        expected = RefinedResult(markdown="精修后的文本")
        mock_refiner.refine = AsyncMock(return_value=expected)

        result = await Pipeline._refine_one_segment(
            mock_refiner, "原始文本", 0, 1,
        )
        assert result.markdown == "精修后的文本"
        mock_refiner.refine.assert_awaited_once()

        # 验证传入的 RefineContext
        call_args = mock_refiner.refine.call_args
        ctx: RefineContext = call_args[0][1]
        assert ctx.segment_index == 1
        assert ctx.total_segments == 1
