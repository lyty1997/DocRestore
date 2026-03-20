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

"""API 路由测试

使用 httpx.AsyncClient + FixtureOCREngine，不依赖 GPU。
手动初始化 Pipeline 和 TaskManager，绕过 ASGI lifespan。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from docrestore.api.routes import router, set_task_manager
from docrestore.ocr.mock import FixtureOCREngine
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import TaskManager

from ..conftest import TEST_STEMS


@pytest.fixture
def work_dir(
    tmp_path: Path, require_ocr_data: Path
) -> Path:
    """准备工作目录：input/ 放假图片，output/ 放 OCR 数据"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    stems = TEST_STEMS[:4]
    for stem in stems:
        (input_dir / f"{stem}.JPG").write_bytes(b"fake")
        src = require_ocr_data / f"{stem}_OCR"
        if src.exists():
            shutil.copytree(
                src, output_dir / f"{stem}_OCR"
            )

    return tmp_path


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """创建测试客户端，手动初始化 Pipeline + TaskManager"""
    config = PipelineConfig()
    pipeline = Pipeline(config)
    engine = FixtureOCREngine()
    pipeline.set_ocr_engine(engine)
    await pipeline.initialize()

    manager = TaskManager(pipeline)
    set_task_manager(manager)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac

    await pipeline.shutdown()
    set_task_manager(None)  # type: ignore[arg-type]


@pytest.mark.usefixtures("require_ocr_data")
class TestRoutes:
    """API 路由功能测试"""

    @pytest.mark.asyncio
    async def test_create_and_get_task(
        self,
        client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """POST 创建任务 → GET 查询状态"""
        resp = await client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(work_dir / "input"),
                "output_dir": str(work_dir / "output"),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "pending"

        task_id = data["task_id"]

        resp = await client.get(
            f"/api/v1/tasks/{task_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(
        self, client: AsyncClient
    ) -> None:
        """查询不存在的任务返回 404"""
        resp = await client.get(
            "/api/v1/tasks/nonexistent"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_and_get_result(
        self,
        client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """创建任务 → 等待完成 → 获取结果"""
        resp = await client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(work_dir / "input"),
                "output_dir": str(work_dir / "output"),
            },
        )
        task_id = resp.json()["task_id"]

        for _ in range(50):
            resp = await client.get(
                f"/api/v1/tasks/{task_id}"
            )
            status = resp.json()["status"]
            if status in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        resp = await client.get(
            f"/api/v1/tasks/{task_id}/result"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["markdown"] != ""
        assert data["output_path"] != ""

    @pytest.mark.asyncio
    async def test_get_result_nonexistent(
        self, client: AsyncClient
    ) -> None:
        """不存在的任务获取结果返回 404"""
        resp = await client.get(
            "/api/v1/tasks/nonexistent/result"
        )
        assert resp.status_code == 404


class TestValidation:
    """请求校验测试"""

    @pytest.mark.asyncio
    async def test_empty_body(
        self, client: AsyncClient
    ) -> None:
        """空 body 返回 422"""
        resp = await client.post(
            "/api/v1/tasks", json={}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_image_dir(
        self, client: AsyncClient
    ) -> None:
        """缺少 image_dir 返回 422"""
        resp = await client.post(
            "/api/v1/tasks",
            json={"output_dir": "/tmp/out"},  # noqa: S108
        )
        assert resp.status_code == 422
