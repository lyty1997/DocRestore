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

"""assets/download 接口测试（AGE-13）。"""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from docrestore.api.routes import router, set_task_manager
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import TaskManager

from ..conftest import TEST_STEMS
from ..support.ocr_engine import FixtureOCREngine


@pytest.fixture
def work_dir(tmp_path: Path, require_ocr_data: Path) -> Path:
    """准备工作目录：input/ 放假图片，output/ 放 OCR 数据。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    stems = TEST_STEMS[:2]
    for stem in stems:
        (input_dir / f"{stem}.JPG").write_bytes(b"fake")
        src = require_ocr_data / f"{stem}_OCR"
        if src.exists():
            shutil.copytree(src, output_dir / f"{stem}_OCR")

    return tmp_path


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """创建测试客户端，手动初始化 Pipeline + TaskManager。"""
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
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await pipeline.shutdown()
    set_task_manager(None)


async def _wait_task_done(client: AsyncClient, task_id: str) -> None:
    """轮询等待任务终态。"""
    for _ in range(200):
        resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            return
        await asyncio.sleep(0.05)

    raise AssertionError("任务未在预期时间内结束")


@pytest.mark.usefixtures("require_ocr_data")
class TestResultDelivery:
    """结果资源访问与下载测试"""

    @pytest.mark.asyncio
    async def test_assets_prevent_path_traversal(
        self,
        client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """assets 必须防 .. 路径穿越。"""
        resp = await client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(work_dir / "input"),
                "output_dir": str(work_dir / "output"),
            },
        )
        task_id = resp.json()["task_id"]
        await _wait_task_done(client, task_id)

        resp = await client.get(
            f"/api/v1/tasks/{task_id}/assets/../secret"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_download_zip_contains_document_and_images(
        self,
        client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """download 返回 zip，包含 document.md 与 images/。"""
        resp = await client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(work_dir / "input"),
                "output_dir": str(work_dir / "output"),
            },
        )
        task_id = resp.json()["task_id"]
        await _wait_task_done(client, task_id)

        resp = await client.get(
            f"/api/v1/tasks/{task_id}/download"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(
            "application/zip"
        )

        z = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(z.namelist())
        assert "document.md" in names

        # zip 中必须包含 output_dir/images/ 下实际存在的文件（若无插图则允许为空）
        output_images_dir = (work_dir / "output") / "images"
        expected_images = []
        if output_images_dir.exists():
            for p in sorted(output_images_dir.rglob("*")):
                if p.is_file():
                    expected_images.append(
                        p.relative_to(work_dir / "output").as_posix()
                    )

        for img in expected_images:
            assert img in names

        doc = z.read("document.md").decode("utf-8")

        # 从 markdown 图片语法中提取 images/ 引用，
        # 避免误把说明文本里的 images/ 当成引用
        refs = re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", doc)

        # 若输出目录存在图片，则 markdown 中应至少引用到 1 张图片
        if expected_images:
            assert refs

        for r in refs:
            assert r in names
