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

"""POST /tasks/{id}/crop-figure（编辑模式手动重截插图）端点测试。

覆盖：正常裁剪落 images/、多文档 doc_dir 子目录、404（任务/源图缺失）、
400（路径穿越源图名 / 非法 doc_dir）。源图用 cv2 写真图（端点要 imread 成功）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

import cv2  # noqa: E402
from httpx import AsyncClient  # noqa: E402

from docrestore.api import routes  # noqa: E402
from docrestore.pipeline.task_manager import Task, TaskStatus  # noqa: E402


def _inject_task(task_id: str, tmp_path: Path) -> tuple[Path, Path]:
    """注入 Task + 写真源图 page.jpg（白底 400x600）。返回 (image_dir, output_dir)。"""
    assert routes._task_manager is not None
    img_dir = tmp_path / f"imgs_{task_id}"
    img_dir.mkdir(parents=True, exist_ok=True)
    img = np.full((400, 600, 3), 255, np.uint8)  # h=400, w=600
    cv2.imwrite(str(img_dir / "page.jpg"), img)
    out_dir = tmp_path / f"out_{task_id}"
    task = Task(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        image_dir=str(img_dir),
        output_dir=str(out_dir),
    )
    routes._task_manager._tasks[task_id] = task
    return img_dir, out_dir


class TestCropFigure:
    """POST /tasks/{id}/crop-figure"""

    @pytest.mark.asyncio
    async def test_crops_region_into_images(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """按框裁出子图落 output_dir/images/manual_1.jpg，返回相对引用。"""
        _img, out_dir = _inject_task("t-crop", tmp_path)
        resp = await api_client.post(
            "/api/v1/tasks/t-crop/crop-figure",
            json={
                "source_filename": "page.jpg",
                "box": {"x0": 50, "y0": 30, "x1": 350, "y1": 230},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["asset_path"] == "images/manual_1.jpg"
        saved = cv2.imread(str(out_dir / "images" / "manual_1.jpg"))
        assert saved is not None
        assert saved.shape[1] == 300  # x[50,350]
        assert saved.shape[0] == 200  # y[30,230]

    @pytest.mark.asyncio
    async def test_doc_dir_lands_in_subdir(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """多文档 doc_dir：裁剪图落 {doc_dir}/images/，引用仍相对 images/。"""
        _img, out_dir = _inject_task("t-sub", tmp_path)
        resp = await api_client.post(
            "/api/v1/tasks/t-sub/crop-figure",
            json={
                "source_filename": "page.jpg",
                "box": {"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                "doc_dir": "doc-1",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["asset_path"] == "images/manual_1.jpg"
        assert (out_dir / "doc-1" / "images" / "manual_1.jpg").is_file()

    @pytest.mark.asyncio
    async def test_404_when_task_missing(
        self, api_client: AsyncClient,
    ) -> None:
        resp = await api_client.post(
            "/api/v1/tasks/ghost/crop-figure",
            json={
                "source_filename": "page.jpg",
                "box": {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
            },
        )
        assert resp.status_code == 404
        assert "任务不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_404_when_source_missing(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task("t-nosrc", tmp_path)
        resp = await api_client.post(
            "/api/v1/tasks/t-nosrc/crop-figure",
            json={
                "source_filename": "ghost.jpg",
                "box": {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
            },
        )
        assert resp.status_code == 404
        assert "源图不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_400_on_source_traversal(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task("t-trav", tmp_path)
        resp = await api_client.post(
            "/api/v1/tasks/t-trav/crop-figure",
            json={
                "source_filename": "../escape.jpg",
                "box": {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
            },
        )
        assert resp.status_code == 400
        assert "非法文件名" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_400_on_bad_doc_dir(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task("t-baddoc", tmp_path)
        resp = await api_client.post(
            "/api/v1/tasks/t-baddoc/crop-figure",
            json={
                "source_filename": "page.jpg",
                "box": {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
                "doc_dir": "../evil",
            },
        )
        assert resp.status_code == 400
        assert "非法文档目录" in resp.json()["detail"]
