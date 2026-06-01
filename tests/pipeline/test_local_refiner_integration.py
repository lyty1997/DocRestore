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

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.local import LocalLLMRefiner
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
        # PII 云端门控按 isinstance(CloudLLMRefiner) 区分本地/云端 refiner，
        # 本地 refiner 必须不命中该分支
        assert not isinstance(refiner, CloudLLMRefiner)
