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

"""任务管理操作测试（cancel / delete / retry）

状态依赖型分支（运行中拒绝、非失败任务不可重试）通过**显式注入** Task
到 TaskManager._tasks 进行测试，避免依赖 FixtureOCREngine 的时序。
这样拒绝分支一定会被执行到，不会因为任务跑得太快退化为"运行中=已完成"。
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
    *,
    with_output_file: bool = False,
) -> Task:
    """直接向 TaskManager 注入指定状态的 Task。

    用于测试状态机分支（而非端到端流程）。必须在 api_client fixture 之后调用。
    """
    assert routes._task_manager is not None
    img_dir = tmp_path / f"imgs_{task_id}"
    img_dir.mkdir(parents=True, exist_ok=True)
    out_dir = tmp_path / f"out_{task_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if with_output_file:
        (out_dir / "document.md").write_text("# ok", encoding="utf-8")

    task = Task(
        task_id=task_id,
        status=status,
        image_dir=str(img_dir),
        output_dir=str(out_dir),
    )
    routes._task_manager._tasks[task_id] = task
    return task


class TestCancelTask:
    """POST /tasks/{task_id}/cancel"""

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(
        self, api_client: AsyncClient,
    ) -> None:
        """取消不存在的任务返回 404。"""
        resp = await api_client.post("/api/v1/tasks/ghost/cancel")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_pending_task_succeeds(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """取消 PENDING 任务 → 200 + 状态转 FAILED + error='用户取消'。"""
        task = _inject_task(
            "t-cancel-pending", TaskStatus.PENDING, tmp_path,
        )
        resp = await api_client.post(
            f"/api/v1/tasks/{task.task_id}/cancel",
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "任务已取消"
        # 验证状态机真的转移了
        assert task.status == TaskStatus.FAILED
        assert task.error == "用户取消"

    @pytest.mark.asyncio
    async def test_cancel_processing_task_succeeds(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """取消 PROCESSING 任务 → 200 + 状态转 FAILED。"""
        task = _inject_task(
            "t-cancel-proc", TaskStatus.PROCESSING, tmp_path,
        )
        resp = await api_client.post(
            f"/api/v1/tasks/{task.task_id}/cancel",
        )
        assert resp.status_code == 200
        assert task.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_cancel_completed_task_rejected(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """取消 COMPLETED 任务 → 409 + 错误消息明示当前状态。"""
        _inject_task(
            "t-cancel-done", TaskStatus.COMPLETED, tmp_path,
        )
        resp = await api_client.post(
            "/api/v1/tasks/t-cancel-done/cancel",
        )
        assert resp.status_code == 409
        assert "completed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_cancel_failed_task_rejected(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """取消 FAILED 任务 → 409（已终结不可再取消）。"""
        _inject_task(
            "t-cancel-failed", TaskStatus.FAILED, tmp_path,
        )
        resp = await api_client.post(
            "/api/v1/tasks/t-cancel-failed/cancel",
        )
        assert resp.status_code == 409
        assert "failed" in resp.json()["detail"]


class TestDeleteTask:
    """DELETE /tasks/{task_id}"""

    @pytest.mark.asyncio
    async def test_delete_nonexistent(
        self, api_client: AsyncClient,
    ) -> None:
        """删除不存在的任务返回 404。"""
        resp = await api_client.delete("/api/v1/tasks/ghost")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_pending_task_rejected(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """删除 PENDING 任务 → 409 '任务运行中，请先取消'。"""
        _inject_task(
            "t-del-pending", TaskStatus.PENDING, tmp_path,
        )
        resp = await api_client.delete(
            "/api/v1/tasks/t-del-pending",
        )
        assert resp.status_code == 409
        assert "运行中" in resp.json()["detail"]
        # 任务仍在（未被误删）
        assert "t-del-pending" in routes._task_manager._tasks  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_delete_processing_task_rejected(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """删除 PROCESSING 任务 → 409。"""
        _inject_task(
            "t-del-proc", TaskStatus.PROCESSING, tmp_path,
        )
        resp = await api_client.delete("/api/v1/tasks/t-del-proc")
        assert resp.status_code == 409
        assert "运行中" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_completed_task_succeeds(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """删除 COMPLETED 任务 → 200 + 内存移除 + 输出目录清理。"""
        task = _inject_task(
            "t-del-done",
            TaskStatus.COMPLETED,
            tmp_path,
            with_output_file=True,
        )
        out_dir = Path(task.output_dir)
        assert out_dir.exists()  # noqa: ASYNC240
        assert (out_dir / "document.md").exists()  # noqa: ASYNC240

        resp = await api_client.delete("/api/v1/tasks/t-del-done")
        assert resp.status_code == 200
        assert resp.json()["message"] == "任务及产物已删除"

        # 内存真实移除
        assert "t-del-done" not in (
            routes._task_manager._tasks  # type: ignore[union-attr]
        )
        # 输出目录真实清理
        assert not out_dir.exists()  # noqa: ASYNC240

        # GET 再查 → 404
        get = await api_client.get("/api/v1/tasks/t-del-done")
        assert get.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_failed_task_succeeds(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """删除 FAILED 任务 → 200。"""
        _inject_task(
            "t-del-failed", TaskStatus.FAILED, tmp_path,
        )
        resp = await api_client.delete("/api/v1/tasks/t-del-failed")
        assert resp.status_code == 200


class TestRetryTask:
    """POST /tasks/{task_id}/retry"""

    @pytest.mark.asyncio
    async def test_retry_nonexistent(
        self, api_client: AsyncClient,
    ) -> None:
        resp = await api_client.post("/api/v1/tasks/ghost/retry")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_pending_task_rejected(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """重试 PENDING 任务 → 409 '仅失败任务可重试'（真正走拒绝分支）。"""
        _inject_task(
            "t-retry-pending", TaskStatus.PENDING, tmp_path,
        )
        resp = await api_client.post(
            "/api/v1/tasks/t-retry-pending/retry",
        )
        assert resp.status_code == 409
        # 错误消息包含当前状态与拒绝原因
        detail = resp.json()["detail"]
        assert "pending" in detail
        assert "失败任务" in detail

    @pytest.mark.asyncio
    async def test_retry_processing_task_rejected(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """重试 PROCESSING 任务 → 409。"""
        _inject_task(
            "t-retry-proc", TaskStatus.PROCESSING, tmp_path,
        )
        resp = await api_client.post(
            "/api/v1/tasks/t-retry-proc/retry",
        )
        assert resp.status_code == 409
        assert "processing" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_retry_completed_task_rejected(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """重试 COMPLETED 任务 → 409（已完成无需重试）。"""
        _inject_task(
            "t-retry-done", TaskStatus.COMPLETED, tmp_path,
        )
        resp = await api_client.post(
            "/api/v1/tasks/t-retry-done/retry",
        )
        assert resp.status_code == 409
        assert "completed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_retry_failed_task_creates_new_task(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """重试 FAILED 任务 → 200 + 新 task_id + 原配置继承。"""
        old = _inject_task(
            "t-retry-failed", TaskStatus.FAILED, tmp_path,
        )
        resp = await api_client.post(
            f"/api/v1/tasks/{old.task_id}/retry",
        )
        assert resp.status_code == 200
        new_id = resp.json()["task_id"]
        # 新 task 与旧的不同
        assert new_id != old.task_id
        assert resp.json()["message"] == "已创建重试任务"

        # 原 FAILED 任务仍然存在（未被删除）
        assert old.task_id in (
            routes._task_manager._tasks  # type: ignore[union-attr]
        )
        # 新任务存在于 TaskManager 中（配置继承自原任务）
        new_task = routes._task_manager._tasks.get(new_id)  # type: ignore[union-attr]
        assert new_task is not None
        assert new_task.image_dir == old.image_dir


class TestCleanupTasks:
    """POST /tasks/cleanup — 批量清理终态任务"""

    @pytest.mark.asyncio
    async def test_cleanup_rejects_empty_statuses(
        self, api_client: AsyncClient,
    ) -> None:
        resp = await api_client.post(
            "/api/v1/tasks/cleanup", json={"statuses": []},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_cleanup_rejects_non_terminal_status(
        self, api_client: AsyncClient,
    ) -> None:
        """拒绝清理 pending / processing（安全兜底，防止误删运行中任务）。"""
        resp = await api_client.post(
            "/api/v1/tasks/cleanup",
            json={"statuses": ["pending", "completed"]},
        )
        assert resp.status_code == 400
        assert "pending" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_cleanup_deletes_completed_and_failed(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """清理 completed + failed，pending/processing 一个都不能被删。"""
        t_done = _inject_task(
            "t-clean-done", TaskStatus.COMPLETED, tmp_path,
            with_output_file=True,
        )
        t_failed = _inject_task(
            "t-clean-failed", TaskStatus.FAILED, tmp_path,
            with_output_file=True,
        )
        _inject_task("t-clean-pending", TaskStatus.PENDING, tmp_path)
        _inject_task("t-clean-proc", TaskStatus.PROCESSING, tmp_path)

        resp = await api_client.post(
            "/api/v1/tasks/cleanup",
            json={"statuses": ["completed", "failed"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 2
        assert body["failed"] == 0
        assert set(body["deleted_ids"]) == {
            "t-clean-done", "t-clean-failed",
        }

        tasks = routes._task_manager._tasks  # type: ignore[union-attr]
        # 终态任务被删
        assert "t-clean-done" not in tasks
        assert "t-clean-failed" not in tasks
        # 非终态任务保留
        assert "t-clean-pending" in tasks
        assert "t-clean-proc" in tasks
        # 输出目录被清理
        assert not Path(t_done.output_dir).exists()  # noqa: ASYNC240
        assert not Path(t_failed.output_dir).exists()  # noqa: ASYNC240

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_nothing_matches(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        _inject_task("t-clean-alone", TaskStatus.PENDING, tmp_path)
        resp = await api_client.post(
            "/api/v1/tasks/cleanup",
            json={"statuses": ["completed", "failed"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 0
        assert body["failed"] == 0
        assert body["deleted_ids"] == []
