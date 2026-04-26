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

"""TaskManager 单元测试（纯 mock，CI 友好）

覆盖：
- create_task：生成 task_id、默认 output_dir、写入内存
- get_task / get_task_async：内存优先 + DB fallback
- update_result_markdown：状态/索引校验、落盘、内存同步
- cancel_task：不存在 / 错误状态 / 成功取消并更新
- delete_task：不存在 / 运行中 / 成功删除并清理文件
- retry_task：不存在 / 非 FAILED / 成功创建新任务
- list_tasks：无 DB 时从内存分页 + 按状态过滤
- subscribe_progress / publish_progress：队列背压（maxsize=1）
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.models import PipelineResult, TaskProgress
from docrestore.persistence.database import TaskDatabase, TaskRow
from docrestore.pipeline.task_manager import Task, TaskManager, TaskStatus


def _make_manager(
    *, db: TaskDatabase | None = None,
) -> TaskManager:
    """构造不启动 Pipeline 的 TaskManager。"""
    pipeline = MagicMock()
    # TaskManager 只在运行时读 pipeline.config.debug / process_tree，
    # 这里 MagicMock 足以。
    return TaskManager(pipeline=pipeline, scheduler=None, db=db)


def _make_completed_task(
    task_id: str,
    output_dir: Path,
    results: list[PipelineResult],
) -> Task:
    """直接构造一个 COMPLETED 的 Task（跳过 pipeline）。"""
    return Task(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        image_dir=str(output_dir / "imgs"),
        output_dir=str(output_dir),
        results=results,
    )


class TestCreateTask:
    """create_task 基本行为"""

    def test_create_task_generates_id_and_default_output_dir(self) -> None:
        mgr = _make_manager()
        task = mgr.create_task(image_dir="/tmp/imgs")  # noqa: S108

        assert task.status is TaskStatus.PENDING
        assert task.image_dir == "/tmp/imgs"  # noqa: S108
        # 默认 output_dir 为 tempfile 下的 docrestore_<task_id>
        assert task.output_dir.endswith(f"docrestore_{task.task_id}")
        # task_id 为 8 位 hex
        assert len(task.task_id) == 8
        # 写入了内存
        assert mgr.get_task(task.task_id) is task

    def test_create_task_respects_explicit_output_dir(self) -> None:
        mgr = _make_manager()
        task = mgr.create_task(
            image_dir="/tmp/i", output_dir="/tmp/o",  # noqa: S108
        )
        assert task.output_dir == "/tmp/o"  # noqa: S108


class TestGetTaskAsync:
    """get_task_async：内存优先 + DB fallback"""

    @pytest.mark.asyncio
    async def test_returns_memory_hit_without_db_call(self) -> None:
        db = AsyncMock(spec=TaskDatabase)
        mgr = _make_manager(db=db)
        task = mgr.create_task(image_dir="/x")
        # 避免触发后台持久化对 AsyncMock 的实际调用
        db.insert_task = AsyncMock(return_value=None)

        result = await mgr.get_task_async(task.task_id)

        assert result is task
        db.get_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_memory_no_db(self) -> None:
        mgr = _make_manager()
        result = await mgr.get_task_async("unknown-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_db_on_memory_miss(self) -> None:
        db = AsyncMock(spec=TaskDatabase)
        db.get_task = AsyncMock(
            return_value=TaskRow(
                task_id="zzz",
                status="failed",
                image_dir="/x",
                output_dir="/y",
                llm=None,
                ocr=None,
                pii=None,
                code=None,
                error="boom",
                created_at="2026-04-08T10:00:00",
                updated_at="2026-04-08T11:00:00",
            ),
        )
        mgr = _make_manager(db=db)

        result = await mgr.get_task_async("zzz")

        assert result is not None
        assert result.task_id == "zzz"
        assert result.status is TaskStatus.FAILED
        assert result.error == "boom"
        db.get_task.assert_awaited_once_with("zzz")

    @pytest.mark.asyncio
    async def test_db_exception_returns_none(self) -> None:
        """DB 查询异常时应返回 None 而不是抛出。"""
        db = AsyncMock(spec=TaskDatabase)
        db.get_task = AsyncMock(side_effect=RuntimeError("db broken"))
        mgr = _make_manager(db=db)

        result = await mgr.get_task_async("zzz")
        assert result is None


class TestUpdateResultMarkdown:
    """update_result_markdown：状态/索引校验 + 落盘"""

    @pytest.mark.asyncio
    async def test_rejects_missing_task(self, tmp_path: Path) -> None:
        mgr = _make_manager()
        err = await mgr.update_result_markdown(
            "nope", 0, "new content",
        )
        assert err == "任务不存在"

    @pytest.mark.asyncio
    async def test_rejects_incomplete_task(self, tmp_path: Path) -> None:
        mgr = _make_manager()
        task = mgr.create_task(image_dir=str(tmp_path))
        # 仍是 PENDING
        err = await mgr.update_result_markdown(
            task.task_id, 0, "x",
        )
        assert err == "任务未完成，无法编辑"

    @pytest.mark.asyncio
    async def test_rejects_index_out_of_range(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_manager()
        result = PipelineResult(
            output_path=tmp_path / "doc.md",
            markdown="原文",
        )
        (tmp_path / "doc.md").write_text("原文", encoding="utf-8")
        task = _make_completed_task("t1", tmp_path, [result])
        mgr._tasks[task.task_id] = task

        assert (
            await mgr.update_result_markdown(task.task_id, -1, "x")
            == "文档索引越界"
        )
        assert (
            await mgr.update_result_markdown(task.task_id, 99, "x")
            == "文档索引越界"
        )

    @pytest.mark.asyncio
    async def test_success_writes_file_and_updates_memory(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_manager()
        out = tmp_path / "doc.md"
        out.write_text("旧内容", encoding="utf-8")
        result = PipelineResult(output_path=out, markdown="旧内容")
        task = _make_completed_task("t2", tmp_path, [result])
        mgr._tasks[task.task_id] = task

        err = await mgr.update_result_markdown(
            task.task_id, 0, "新内容",
        )
        assert err is None

        # 落盘生效
        assert out.read_text(encoding="utf-8") == "新内容"
        # 内存同步
        assert task.results[0].markdown == "新内容"


class TestCancelTask:
    """cancel_task 三分支"""

    @pytest.mark.asyncio
    async def test_returns_none_when_task_missing(self) -> None:
        mgr = _make_manager()
        assert await mgr.cancel_task("nope") is None

    @pytest.mark.asyncio
    async def test_rejects_completed_task(self, tmp_path: Path) -> None:
        mgr = _make_manager()
        task = _make_completed_task("done", tmp_path, [])
        mgr._tasks[task.task_id] = task

        err = await mgr.cancel_task(task.task_id)
        assert err is not None
        assert "无法取消" in err

    @pytest.mark.asyncio
    async def test_cancels_pending_task(self) -> None:
        mgr = _make_manager()
        task = mgr.create_task(image_dir="/x")

        err = await mgr.cancel_task(task.task_id)

        assert err == ""
        assert task.status is TaskStatus.FAILED
        assert task.error == "用户取消"

    @pytest.mark.asyncio
    async def test_cancels_running_task_and_triggers_bg_cancel(
        self,
    ) -> None:
        """注册了后台 asyncio.Task 时应调用其 cancel()。"""
        mgr = _make_manager()
        task = mgr.create_task(image_dir="/x")

        # 构造一个长时运行的 asyncio.Task，用于验证 cancel() 被调用
        async def _forever() -> None:
            await asyncio.sleep(3600)

        bg = asyncio.create_task(_forever())
        mgr.register_running_task(task.task_id, bg)

        err = await mgr.cancel_task(task.task_id)
        assert err == ""
        # 让 loop 处理一次 cancel 传播
        await asyncio.sleep(0)
        assert bg.cancelled() or bg.done()


class TestDeleteTask:
    """delete_task 三分支"""

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self) -> None:
        mgr = _make_manager()
        assert await mgr.delete_task("nope") is None

    @pytest.mark.asyncio
    async def test_rejects_running_task(self) -> None:
        mgr = _make_manager()
        task = mgr.create_task(image_dir="/x")
        task.status = TaskStatus.PROCESSING

        err = await mgr.delete_task(task.task_id)
        assert err == "任务运行中，请先取消"

    @pytest.mark.asyncio
    async def test_removes_output_dir_and_memory(
        self, tmp_path: Path,
    ) -> None:
        mgr = _make_manager()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "doc.md").write_text("x", encoding="utf-8")

        task = _make_completed_task("dd", out_dir, [])
        mgr._tasks[task.task_id] = task

        err = await mgr.delete_task(task.task_id)

        assert err == ""
        assert not out_dir.exists()
        assert mgr.get_task(task.task_id) is None

    @pytest.mark.asyncio
    async def test_deletes_via_db_when_configured(
        self, tmp_path: Path,
    ) -> None:
        """配置了 DB 时应调用 db.delete_task。"""
        db = AsyncMock(spec=TaskDatabase)
        db.delete_task = AsyncMock(return_value=True)
        mgr = _make_manager(db=db)
        task = _make_completed_task("dd", tmp_path, [])
        mgr._tasks[task.task_id] = task

        await mgr.delete_task(task.task_id)

        db.delete_task.assert_awaited_once_with(task.task_id)


class TestRetryTask:
    """retry_task 三分支"""

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self) -> None:
        mgr = _make_manager()
        assert await mgr.retry_task("nope") is None

    @pytest.mark.asyncio
    async def test_rejects_non_failed_task(self, tmp_path: Path) -> None:
        mgr = _make_manager()
        task = _make_completed_task("done", tmp_path, [])
        mgr._tasks[task.task_id] = task

        err = await mgr.retry_task(task.task_id)
        assert isinstance(err, str)
        assert "仅失败任务可重试" in err

    @pytest.mark.asyncio
    async def test_creates_new_task_from_failed(self) -> None:
        mgr = _make_manager()
        failed = Task(
            task_id="failed-1",
            status=TaskStatus.FAILED,
            image_dir="/orig/imgs",
            output_dir="/orig/out",
            error="oops",
        )
        mgr._tasks[failed.task_id] = failed

        new = await mgr.retry_task(failed.task_id)

        assert isinstance(new, Task)
        assert new.task_id != failed.task_id
        assert new.status is TaskStatus.PENDING
        assert new.image_dir == failed.image_dir


class TestListTasksInMemory:
    """无 DB 时从内存分页"""

    @pytest.mark.asyncio
    async def test_list_paginates_by_created_desc(self) -> None:
        mgr = _make_manager()

        # 插入 3 个任务（递增 created_at），期望按倒序返回
        def _insert(tid: str, ts: str, status: TaskStatus) -> None:
            mgr._tasks[tid] = Task(
                task_id=tid,
                status=status,
                image_dir="/",
                output_dir="/",
                created_at=datetime.fromisoformat(ts),
            )

        _insert("t-old", "2026-01-01T00:00:00", TaskStatus.COMPLETED)
        _insert("t-mid", "2026-02-01T00:00:00", TaskStatus.COMPLETED)
        _insert("t-new", "2026-03-01T00:00:00", TaskStatus.FAILED)

        page = await mgr.list_tasks(page=1, page_size=2)
        assert page.total == 3
        assert [t.task_id for t in page.tasks] == ["t-new", "t-mid"]

        page2 = await mgr.list_tasks(page=2, page_size=2)
        assert [t.task_id for t in page2.tasks] == ["t-old"]

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self) -> None:
        mgr = _make_manager()
        mgr._tasks["a"] = Task(
            task_id="a",
            status=TaskStatus.COMPLETED,
            image_dir="/",
            output_dir="/",
        )
        mgr._tasks["b"] = Task(
            task_id="b",
            status=TaskStatus.FAILED,
            image_dir="/",
            output_dir="/",
        )

        only_failed = await mgr.list_tasks(status="failed")
        assert only_failed.total == 1
        assert only_failed.tasks[0].task_id == "b"


class TestCollectReferencedImageDirs:
    """collect_referenced_image_dirs：合并内存 + DB 的 image_dir。

    upload 清理循环用这个集合跳过"仍被任务引用的 upload_dir"，修复
    2026-04-23 的烂图预览 bug（task 复用 upload_dir，1h 后被 rmtree）。
    """

    @pytest.mark.asyncio
    async def test_memory_only_when_no_db(self, tmp_path: Path) -> None:
        mgr = _make_manager()
        dir_a = str(tmp_path / "a")
        dir_b = str(tmp_path / "b")
        mgr._tasks["a"] = Task(
            task_id="a", status=TaskStatus.COMPLETED,
            image_dir=dir_a, output_dir="/",
        )
        mgr._tasks["b"] = Task(
            task_id="b", status=TaskStatus.PROCESSING,
            image_dir=dir_b, output_dir="/",
        )
        mgr._tasks["c"] = Task(
            task_id="c", status=TaskStatus.FAILED,
            image_dir="", output_dir="/",  # 空串应被过滤
        )

        dirs = await mgr.collect_referenced_image_dirs()
        assert dirs == {dir_a, dir_b}

    @pytest.mark.asyncio
    async def test_merges_memory_and_db(self, tmp_path: Path) -> None:
        from docrestore.persistence.database import (
            TaskListItem as DBTaskListItem,
        )
        from docrestore.persistence.database import TaskListResult

        db = MagicMock(spec=TaskDatabase)
        dir_mem = str(tmp_path / "mem-only")
        dir_db = str(tmp_path / "from-db")

        def _fake_list(
            status: str,
            page: int,
            page_size: int,
        ) -> TaskListResult:
            # db 里再多一个 dir_db 的已完成任务
            if status == "completed" and page == 1:
                return TaskListResult(
                    tasks=[
                        DBTaskListItem(
                            task_id="db1",
                            status="completed",
                            image_dir=dir_db,
                            output_dir="/",
                            error=None,
                            created_at="2026-04-23T00:00:00",
                            result_count=1,
                        ),
                    ],
                    total=1,
                    page=1,
                    page_size=page_size,
                )
            return TaskListResult(
                tasks=[], total=0, page=page, page_size=page_size,
            )

        db.list_tasks = AsyncMock(side_effect=_fake_list)

        mgr = _make_manager(db=db)
        mgr._tasks["mem1"] = Task(
            task_id="mem1", status=TaskStatus.PROCESSING,
            image_dir=dir_mem, output_dir="/",
        )

        dirs = await mgr.collect_referenced_image_dirs()
        assert dirs == {dir_mem, dir_db}

    @pytest.mark.asyncio
    async def test_db_exception_does_not_break(
        self, tmp_path: Path,
    ) -> None:
        """DB 故障时只从内存收集，保守返回；不让 cleanup 崩。"""
        db = MagicMock(spec=TaskDatabase)
        db.list_tasks = AsyncMock(side_effect=RuntimeError("db down"))

        dir_m = str(tmp_path / "m")
        mgr = _make_manager(db=db)
        mgr._tasks["m"] = Task(
            task_id="m", status=TaskStatus.COMPLETED,
            image_dir=dir_m, output_dir="/",
        )

        dirs = await mgr.collect_referenced_image_dirs()
        assert dirs == {dir_m}


class TestProgressPubSub:
    """进度发布/订阅"""

    @pytest.mark.asyncio
    async def test_subscribe_returns_queue_for_existing_task(self) -> None:
        mgr = _make_manager()
        task = mgr.create_task(image_dir="/x")

        q = await mgr.subscribe_progress(task.task_id)
        assert q is not None
        assert q.maxsize == 1
        assert mgr.subscriber_count(task.task_id) == 1

        await mgr.unsubscribe_progress(task.task_id, q)
        assert mgr.subscriber_count(task.task_id) == 0

    @pytest.mark.asyncio
    async def test_subscribe_missing_task_returns_none(self) -> None:
        mgr = _make_manager()
        q = await mgr.subscribe_progress("nope")
        assert q is None

    @pytest.mark.asyncio
    async def test_publish_progress_delivers_latest_only(self) -> None:
        """maxsize=1 + 背压策略：慢订阅者只保留最新。"""
        mgr = _make_manager()
        task = mgr.create_task(image_dir="/x")
        q = await mgr.subscribe_progress(task.task_id)
        assert q is not None

        p1 = TaskProgress(stage="ocr", current=1, total=10)
        p2 = TaskProgress(stage="ocr", current=5, total=10)

        mgr.publish_progress(task.task_id, p1)
        mgr.publish_progress(task.task_id, p2)
        # 让后台广播 task 执行
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        got = await q.get()
        # 只保留最新
        assert got.current in {1, 5}
        assert q.empty()


class TestShutdown:
    """TaskManager.shutdown：cancel 所有运行中任务。"""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending_running_tasks(self) -> None:
        """挂起的运行中任务被 cancel，shutdown 完成后清空注册表。"""
        mgr = _make_manager()

        task_done_events: list[asyncio.Event] = []

        async def hang_forever() -> None:
            done = asyncio.Event()
            task_done_events.append(done)
            try:
                await asyncio.Future()  # 永远挂起
            except asyncio.CancelledError:
                done.set()
                raise

        bg1 = asyncio.create_task(hang_forever(), name="task1")
        bg2 = asyncio.create_task(hang_forever(), name="task2")
        mgr.register_running_task("tid1", bg1)
        mgr.register_running_task("tid2", bg2)

        # 让挂起任务先 schedule
        await asyncio.sleep(0)
        assert len(task_done_events) == 2
        assert all(not e.is_set() for e in task_done_events)

        await mgr.shutdown()

        # 两个任务都被 cancel 并走到 CancelledError 分支
        assert all(e.is_set() for e in task_done_events)
        assert bg1.done()
        assert bg2.done()
        assert mgr._running_tasks == {}  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_shutdown_noop_when_no_running_tasks(self) -> None:
        """没有运行中任务时 shutdown 快速返回。"""
        mgr = _make_manager()
        # 不注册任何任务，直接 shutdown 应立即返回
        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_swallows_task_exceptions(self) -> None:
        """任务抛非 CancelledError 异常时 shutdown 只记录不重抛。"""
        mgr = _make_manager()

        async def raise_runtime() -> None:
            raise RuntimeError("boom")

        bg = asyncio.create_task(raise_runtime(), name="err-task")
        mgr.register_running_task("err", bg)
        await asyncio.sleep(0)  # 让任务产生异常

        # shutdown 不应把 RuntimeError 抛出来
        await mgr.shutdown()
        assert bg.done()
