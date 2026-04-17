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

"""OCR 引擎按需预热接口测试

覆盖 GET /ocr/status 与 POST /ocr/warmup 的 ready/switching/accepted
三态分支，以及未挂载 EngineManager 时的 500 兜底。
不依赖真实 GPU 与图片：用 MagicMock 模拟 EngineManager。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from docrestore.api.routes import router, set_task_manager
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import TaskManager

from ..support.ocr_engine import FixtureOCREngine

API_PREFIX = "/api/v1"


def _make_engine_manager_mock(
    *,
    model: str = "paddle-ocr/ppocr-v4",
    gpu: str = "1",
    is_ready: bool = True,
    is_switching: bool = False,
) -> MagicMock:
    """构造一个 EngineManager 的鸭子类型替身。"""
    em = MagicMock(name="EngineManager")
    em.current_model = model
    em.current_gpu = gpu
    em.is_ready = is_ready
    em.is_switching = is_switching
    em.ensure = AsyncMock()
    return em


async def _make_app(engine_manager: MagicMock | None) -> tuple[FastAPI, Pipeline]:
    """构造仅挂载 OCR/Task 路由的最小 FastAPI 应用。

    与 conftest.api_client 不同：这里允许选择性挂载 engine_manager，
    以便测试 500 兜底路径。
    """
    config = PipelineConfig()
    pipeline = Pipeline(config)
    pipeline.set_ocr_engine(FixtureOCREngine())
    await pipeline.initialize()

    manager = TaskManager(pipeline)
    set_task_manager(manager)

    app = FastAPI()
    app.include_router(router, prefix=API_PREFIX)
    if engine_manager is not None:
        app.state.engine_manager = engine_manager
    return app, pipeline


@pytest.fixture
async def ocr_client_with_em() -> AsyncIterator[tuple[AsyncClient, MagicMock]]:
    """挂载默认 fake EngineManager（已就绪、模型匹配）的客户端。"""
    em = _make_engine_manager_mock()
    app, pipeline = await _make_app(em)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, em
    await pipeline.shutdown()
    set_task_manager(None)


@pytest.fixture
async def ocr_client_without_em() -> AsyncIterator[AsyncClient]:
    """未挂载 EngineManager 的客户端，用于验证 500 兜底。"""
    app, pipeline = await _make_app(None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await pipeline.shutdown()
    set_task_manager(None)


class TestOcrStatus:
    """GET /ocr/status"""

    @pytest.mark.asyncio
    async def test_returns_engine_state_fields(
        self,
        ocr_client_with_em: tuple[AsyncClient, MagicMock],
    ) -> None:
        """status 字段应原样映射 EngineManager 的当前状态。"""
        client, em = ocr_client_with_em
        em.current_model = "deepseek-ocr-2"
        em.current_gpu = "0"
        em.is_ready = False
        em.is_switching = True

        resp = await client.get(f"{API_PREFIX}/ocr/status")

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["current_model"] == "deepseek-ocr-2"
        assert body["current_gpu"] == "0"
        assert body["is_ready"] is False
        assert body["is_switching"] is True

    @pytest.mark.asyncio
    async def test_returns_500_when_engine_manager_missing(
        self,
        ocr_client_without_em: AsyncClient,
    ) -> None:
        """未挂载 EngineManager → 500 + 中文错误提示。"""
        resp = await ocr_client_without_em.get(f"{API_PREFIX}/ocr/status")

        assert resp.status_code == 500
        assert "EngineManager" in resp.json()["detail"]


class TestOcrWarmup:
    """POST /ocr/warmup"""

    @pytest.mark.asyncio
    async def test_returns_ready_when_already_matched(
        self,
        ocr_client_with_em: tuple[AsyncClient, MagicMock],
    ) -> None:
        """已就绪且模型/GPU 都匹配 → 直接 ready，不触发 ensure。"""
        client, em = ocr_client_with_em
        em.current_model = "paddle-ocr/ppocr-v4"
        em.current_gpu = "1"
        em.is_ready = True
        em.is_switching = False

        resp = await client.post(
            f"{API_PREFIX}/ocr/warmup",
            json={"model": "paddle-ocr/ppocr-v4", "gpu_id": "1"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ready", "message": "引擎已就绪"}
        em.ensure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_switching_when_lock_held(
        self,
        ocr_client_with_em: tuple[AsyncClient, MagicMock],
    ) -> None:
        """is_switching=True → 拒绝重入式预热，返回 switching。"""
        client, em = ocr_client_with_em
        em.is_ready = False
        em.is_switching = True

        resp = await client.post(
            f"{API_PREFIX}/ocr/warmup",
            json={"model": "deepseek-ocr-2", "gpu_id": "0"},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "switching"
        em.ensure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepted_triggers_background_ensure(
        self,
        ocr_client_with_em: tuple[AsyncClient, MagicMock],
    ) -> None:
        """模型/GPU 不匹配且未在切换 → accepted + 后台调用 em.ensure 一次。"""
        client, em = ocr_client_with_em
        em.current_model = "paddle-ocr/ppocr-v4"
        em.current_gpu = "1"
        em.is_ready = True
        em.is_switching = False

        resp = await client.post(
            f"{API_PREFIX}/ocr/warmup",
            json={"model": "deepseek-ocr-2", "gpu_id": "0"},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

        # 让事件循环把 create_task() 调度的协程跑完
        for _ in range(20):
            if em.ensure.await_count >= 1:
                break
            await asyncio.sleep(0.01)

        em.ensure.assert_awaited_once()
        # ensure 收到的应是 model_copy 后带新 model/gpu_id 的 OCRConfig
        called_config = em.ensure.await_args.args[0]
        assert called_config.model == "deepseek-ocr-2"
        assert called_config.gpu_id == "0"
