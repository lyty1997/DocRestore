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

import io
import re
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from ..conftest import wait_task_done


@pytest.mark.usefixtures("require_ocr_data")
class TestResultDelivery:
    """结果资源访问与下载测试"""

    @pytest.mark.asyncio
    async def test_assets_prevent_path_traversal(
        self,
        api_client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """assets 必须防 .. 路径穿越。"""
        resp = await api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(work_dir / "input"),
                "output_dir": str(work_dir / "output"),
            },
        )
        task_id = resp.json()["task_id"]
        await wait_task_done(api_client, task_id)

        resp = await api_client.get(
            f"/api/v1/tasks/{task_id}/assets/../secret"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_download_zip_contains_document_and_images(
        self,
        api_client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """download 返回 zip，包含 document.md 与 images/。"""
        resp = await api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(work_dir / "input"),
                "output_dir": str(work_dir / "output"),
            },
        )
        task_id = resp.json()["task_id"]
        await wait_task_done(api_client, task_id)

        resp = await api_client.get(
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
