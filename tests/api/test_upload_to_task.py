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

"""上传 → 完成 → 创建任务 端到端链路

验证三段 API 能连起来：
POST /uploads
  → POST /uploads/{sid}/files
  → POST /uploads/{sid}/complete (得到 image_dir)
  → POST /tasks { image_dir }

不验证 Pipeline 执行成功（FixtureOCREngine 无 _OCR 数据会 failed），
只验证链路通畅：任务可创建、source-images 返回上传的文件名。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import wait_task_done


async def _create_session_with_files(
    api_client: AsyncClient, filenames: list[str],
) -> tuple[str, str]:
    """创建上传会话并上传指定文件。返回 (session_id, image_dir)。"""
    resp = await api_client.post("/api/v1/uploads")
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    files = [
        ("files", (name, b"fake_bytes_for_" + name.encode(), "image/jpeg"))
        for name in filenames
    ]
    upload = await api_client.post(
        f"/api/v1/uploads/{sid}/files", files=files,
    )
    assert upload.status_code == 200

    complete = await api_client.post(f"/api/v1/uploads/{sid}/complete")
    assert complete.status_code == 200
    data = complete.json()
    return sid, data["image_dir"]


class TestUploadToTaskFlow:
    """POST /uploads → /files → /complete → POST /tasks 端到端"""

    @pytest.mark.asyncio
    async def test_upload_complete_creates_task(
        self, api_client: AsyncClient,
    ) -> None:
        """上传 2 张图 → 完成 → image_dir 用于创建任务 → 返回 task_id。"""
        _, image_dir = await _create_session_with_files(
            api_client, ["a.jpg", "b.jpg"],
        )

        create = await api_client.post(
            "/api/v1/tasks", json={"image_dir": image_dir},
        )
        assert create.status_code == 200
        task_id = create.json()["task_id"]
        assert len(task_id) >= 6

        # GET 能查到该任务
        get = await api_client.get(f"/api/v1/tasks/{task_id}")
        assert get.status_code == 200
        assert get.json()["task_id"] == task_id

    @pytest.mark.asyncio
    async def test_uploaded_images_visible_via_source_images(
        self, api_client: AsyncClient,
    ) -> None:
        """创建任务后，source-images 能枚举上传的文件。"""
        names = ["p1.jpg", "p2.jpg", "p3.png"]
        _, image_dir = await _create_session_with_files(api_client, names)

        create = await api_client.post(
            "/api/v1/tasks", json={"image_dir": image_dir},
        )
        task_id = create.json()["task_id"]

        # 等任务跑完（无 _OCR 数据 → 会 failed，但 source-images 仍可读）
        await wait_task_done(api_client, task_id)

        resp = await api_client.get(
            f"/api/v1/tasks/{task_id}/source-images",
        )
        assert resp.status_code == 200
        images = resp.json()["images"]
        assert set(images) == set(names)

    @pytest.mark.asyncio
    async def test_second_complete_rejected(
        self, api_client: AsyncClient,
    ) -> None:
        """同一 session 不能重复 complete。"""
        sid, _ = await _create_session_with_files(
            api_client, ["only.jpg"],
        )
        second = await api_client.post(
            f"/api/v1/uploads/{sid}/complete",
        )
        assert second.status_code == 400

    @pytest.mark.asyncio
    async def test_create_task_with_nonexistent_dir_still_accepted(
        self, api_client: AsyncClient,
    ) -> None:
        """未走 upload，直接传不存在目录 → API 层接受（由 Pipeline 失败）。

        证明 create_task 端点不阻止任意 image_dir，错误在执行阶段暴露。
        """
        create = await api_client.post(
            "/api/v1/tasks", json={"image_dir": "/nonexistent/dir"},
        )
        assert create.status_code == 200
        task_id = create.json()["task_id"]

        await wait_task_done(api_client, task_id)
        get = await api_client.get(f"/api/v1/tasks/{task_id}")
        assert get.json()["status"] == "failed"
