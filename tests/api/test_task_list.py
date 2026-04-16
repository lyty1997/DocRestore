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

"""任务列表接口测试（GET /api/v1/tasks）

通过向 TaskManager._tasks 注入已知状态的 Task，避免依赖
FixtureOCREngine 的时序。这样过滤 / 分页 / 字段断言都是确定性的。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api import routes
from docrestore.pipeline.task_manager import Task, TaskStatus


def _inject_task(
    task_id: str,
    status: TaskStatus,
    tmp_path: Path,
) -> Task:
    """直接向 TaskManager 注入指定状态的 Task。"""
    assert routes._task_manager is not None
    img_dir = tmp_path / f"imgs_{task_id}"
    img_dir.mkdir(parents=True, exist_ok=True)
    out_dir = tmp_path / f"out_{task_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    task = Task(
        task_id=task_id,
        status=status,
        image_dir=str(img_dir),
        output_dir=str(out_dir),
    )
    routes._task_manager._tasks[task_id] = task
    return task


class TestTaskList:
    """任务列表 API 测试"""

    async def test_empty_list(self, api_client: AsyncClient) -> None:
        """无任务时返回空列表。"""
        resp = await api_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    async def test_list_includes_injected_task(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """注入的任务出现在列表中，且字段与注入值一致。"""
        task = _inject_task("t-list-1", TaskStatus.PENDING, tmp_path)

        resp = await api_client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

        items = [t for t in data["tasks"] if t["task_id"] == task.task_id]
        assert len(items) == 1
        item = items[0]
        assert item["status"] == "pending"
        assert item["image_dir"] == task.image_dir
        assert item["output_dir"] == task.output_dir

    async def test_filter_by_status_returns_matching_subset(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """status 过滤必须只返回匹配的任务（确定性验证）。"""
        _inject_task("t-flt-pending", TaskStatus.PENDING, tmp_path)
        _inject_task("t-flt-failed", TaskStatus.FAILED, tmp_path)
        _inject_task("t-flt-done", TaskStatus.COMPLETED, tmp_path)

        # 不过滤 → 3 个都在
        all_resp = await api_client.get("/api/v1/tasks")
        all_ids = {t["task_id"] for t in all_resp.json()["tasks"]}
        assert {"t-flt-pending", "t-flt-failed", "t-flt-done"} <= all_ids

        # 过滤 failed → 只返回 failed
        resp = await api_client.get(
            "/api/v1/tasks", params={"status": "failed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        ids = {t["task_id"] for t in data["tasks"]}
        assert "t-flt-failed" in ids
        assert "t-flt-pending" not in ids
        assert "t-flt-done" not in ids
        # 所有返回项的 status 都是 failed
        assert all(t["status"] == "failed" for t in data["tasks"])
        assert data["total"] == len([
            t for t in data["tasks"] if t["status"] == "failed"
        ])

    async def test_pagination_returns_disjoint_pages(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """分页必须返回不同子集，不是全量重复。"""
        ids = [f"t-pg-{i}" for i in range(5)]
        for tid in ids:
            _inject_task(tid, TaskStatus.FAILED, tmp_path)

        page1 = await api_client.get(
            "/api/v1/tasks", params={"page": 1, "page_size": 2},
        )
        page2 = await api_client.get(
            "/api/v1/tasks", params={"page": 2, "page_size": 2},
        )
        assert page1.status_code == 200
        assert page2.status_code == 200

        p1 = page1.json()
        p2 = page2.json()
        assert p1["total"] == 5
        assert p2["total"] == 5
        assert p1["page"] == 1
        assert p2["page"] == 2
        assert len(p1["tasks"]) == 2
        assert len(p2["tasks"]) == 2

        p1_ids = {t["task_id"] for t in p1["tasks"]}
        p2_ids = {t["task_id"] for t in p2["tasks"]}
        # 关键断言：两页不重叠
        assert p1_ids.isdisjoint(p2_ids)
        # 两页都属于注入的任务
        assert p1_ids <= set(ids)
        assert p2_ids <= set(ids)

    async def test_page_size_clamped(self, api_client: AsyncClient) -> None:
        """page_size 超过 100 时被限制。"""
        resp = await api_client.get(
            "/api/v1/tasks", params={"page_size": 999},
        )
        assert resp.status_code == 200
        assert resp.json()["page_size"] == 100

    async def test_list_item_has_required_fields(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """列表项必须包含所有约定字段。"""
        _inject_task("t-fields-1", TaskStatus.COMPLETED, tmp_path)

        resp = await api_client.get("/api/v1/tasks")
        tasks = resp.json()["tasks"]
        assert len(tasks) >= 1  # 注入过就一定有

        item = next(t for t in tasks if t["task_id"] == "t-fields-1")
        # 必需字段齐全
        for field in (
            "task_id", "status", "image_dir",
            "output_dir", "created_at", "result_count",
        ):
            assert field in item, f"缺少字段: {field}"
        # 注入 COMPLETED 时 result_count 未落盘 → 默认 0
        assert item["status"] == "completed"
        assert item["result_count"] == 0

    async def test_invalid_page_clamped_to_one(
        self, api_client: AsyncClient,
    ) -> None:
        """page<1 被 clamp 为 1（路由层行为）。"""
        resp = await api_client.get(
            "/api/v1/tasks", params={"page": 0},
        )
        assert resp.status_code == 200
        assert resp.json()["page"] == 1

    @pytest.mark.asyncio
    async def test_filter_with_no_matches_returns_empty(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """过滤条件无匹配时 tasks=[] 且 total=0。"""
        _inject_task("t-only-pending", TaskStatus.PENDING, tmp_path)

        resp = await api_client.get(
            "/api/v1/tasks", params={"status": "completed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["total"] == 0
