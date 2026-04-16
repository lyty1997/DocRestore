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

"""源图片端点测试

覆盖：
- GET /tasks/{id}/source-images：列表/404/空目录/递归扫描/扩展名过滤
- GET /tasks/{id}/source-images/{filename}：下载/404/400 路径穿越防护
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api import routes
from docrestore.pipeline.task_manager import Task, TaskStatus


def _inject_task_with_images(
    task_id: str,
    tmp_path: Path,
    filenames: list[str],
    *,
    content_prefix: bytes = b"img_",
) -> Path:
    """注入一个 Task 并在磁盘准备源图片。返回 image_dir。"""
    assert routes._task_manager is not None
    img_dir = tmp_path / f"imgs_{task_id}"
    img_dir.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        path = img_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content_prefix + name.encode())

    task = Task(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        image_dir=str(img_dir),
        output_dir=str(tmp_path / f"out_{task_id}"),
    )
    routes._task_manager._tasks[task_id] = task
    return img_dir


class TestListSourceImages:
    """GET /tasks/{id}/source-images"""

    @pytest.mark.asyncio
    async def test_returns_404_when_task_missing(
        self, api_client: AsyncClient,
    ) -> None:
        resp = await api_client.get(
            "/api/v1/tasks/ghost-task/source-images",
        )
        assert resp.status_code == 404
        assert "任务不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_lists_uploaded_images(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task_with_images(
            "t-list", tmp_path, ["a.jpg", "b.png", "c.jpeg"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-list/source-images",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t-list"
        assert set(data["images"]) == {"a.jpg", "b.png", "c.jpeg"}

    @pytest.mark.asyncio
    async def test_empty_when_image_dir_missing(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """image_dir 不存在 → 返回空列表（不报错）。"""
        assert routes._task_manager is not None
        task = Task(
            task_id="t-no-dir",
            status=TaskStatus.COMPLETED,
            image_dir=str(tmp_path / "does_not_exist"),
            output_dir=str(tmp_path / "out"),
        )
        routes._task_manager._tasks[task.task_id] = task

        resp = await api_client.get(
            f"/api/v1/tasks/{task.task_id}/source-images",
        )
        assert resp.status_code == 200
        assert resp.json()["images"] == []

    @pytest.mark.asyncio
    async def test_recursive_scan_includes_subdirs(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """子目录里的图片也应列出（相对路径）。"""
        _inject_task_with_images(
            "t-sub",
            tmp_path,
            ["top.jpg", "nest/inner.png", "nest/deep/x.jpeg"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-sub/source-images",
        )
        assert resp.status_code == 200
        names = resp.json()["images"]
        assert "top.jpg" in names
        assert "nest/inner.png" in names
        assert "nest/deep/x.jpeg" in names

    @pytest.mark.asyncio
    async def test_filters_non_image_extensions(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """非图片扩展名不应出现在列表中。"""
        _inject_task_with_images(
            "t-ext",
            tmp_path,
            ["real.jpg", "note.txt", "data.json", "raw.bin"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-ext/source-images",
        )
        assert resp.status_code == 200
        names = resp.json()["images"]
        assert names == ["real.jpg"]


class TestGetSourceImage:
    """GET /tasks/{id}/source-images/{filename}"""

    @pytest.mark.asyncio
    async def test_returns_404_when_task_missing(
        self, api_client: AsyncClient,
    ) -> None:
        resp = await api_client.get(
            "/api/v1/tasks/ghost/source-images/a.jpg",
        )
        assert resp.status_code == 404
        assert "任务不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_parent_dir_traversal(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task_with_images(
            "t-trav", tmp_path, ["real.jpg"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-trav/source-images/..%2Fescape.jpg",
        )
        assert resp.status_code == 400
        assert "非法文件名" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_absolute_path(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task_with_images(
            "t-abs", tmp_path, ["real.jpg"],
        )
        # 用 URL 编码的 / 避免被 FastAPI 当路径分隔符丢弃
        resp = await api_client.get(
            "/api/v1/tasks/t-abs/source-images/%2Fetc%2Fpasswd",
        )
        assert resp.status_code == 400
        assert "非法文件名" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_returns_image_bytes(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task_with_images(
            "t-ok", tmp_path, ["good.jpg"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-ok/source-images/good.jpg",
        )
        assert resp.status_code == 200
        assert resp.content == b"img_good.jpg"

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_file(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task_with_images(
            "t-miss", tmp_path, ["exists.jpg"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-miss/source-images/ghost.jpg",
        )
        assert resp.status_code == 404
        assert "图片不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_non_image_extension(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """即便文件存在但扩展名非图片 → 404。"""
        _inject_task_with_images(
            "t-wrong", tmp_path, ["note.txt"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-wrong/source-images/note.txt",
        )
        assert resp.status_code == 404
        assert "图片不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_fetches_nested_image(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """子目录图片通过相对路径访问。"""
        _inject_task_with_images(
            "t-nest", tmp_path, ["sub/dir/pic.png"],
        )
        resp = await api_client.get(
            "/api/v1/tasks/t-nest/source-images/sub/dir/pic.png",
        )
        assert resp.status_code == 200
        assert resp.content == b"img_sub/dir/pic.png"
