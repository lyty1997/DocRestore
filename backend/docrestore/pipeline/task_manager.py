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

"""任务生命周期管理（内存 + SQLite 混合存储）"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import traceback
import uuid
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from docrestore.models import PipelineResult, TaskProgress
from docrestore.pipeline.config import LLMConfig, OCRConfig, PIIConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.scheduler import PipelineScheduler

from docrestore.persistence.database import TaskListItem, TaskListResult

if TYPE_CHECKING:
    from docrestore.persistence.database import TaskDatabase

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """任务记录。

    llm/ocr/pii 为本次任务的完整 Config 快照：
    - None → pipeline 使用默认配置
    - 非空 → 上游（API 层）合成的完整 Config，下游直接使用、不再合并
    """

    task_id: str
    status: TaskStatus
    image_dir: str
    output_dir: str
    llm: LLMConfig | None = None
    ocr: OCRConfig | None = None
    pii: PIIConfig | None = None
    progress: TaskProgress | None = None
    results: list[PipelineResult] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def result(self) -> PipelineResult | None:
        """兼容属性：返回第一份结果。"""
        return self.results[0] if self.results else None


def _write_debug_error(output_dir: Path, content: str) -> None:
    """将完整 traceback 写入 output_dir/debug/error.txt（阻塞 I/O，需 to_thread）。"""
    try:
        debug_dir = output_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "error.txt").write_text(content, encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("写入 debug/error.txt 失败", exc_info=True)


class TaskManager:
    """任务生命周期管理（内存 + SQLite 混合存储）"""

    def __init__(
        self,
        pipeline: Pipeline,
        scheduler: PipelineScheduler | None = None,
        db: TaskDatabase | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._scheduler = scheduler
        self._db = db
        self._tasks: dict[str, Task] = {}
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, set[asyncio.Queue[TaskProgress]]] = {}
        # 追踪所有 fire-and-forget 的后台协程（DB 持久化 / 进度广播 /
        # 引擎预热等）。shutdown 时统一 cancel + gather，避免残留任务
        # 持续跑或在 event loop 关闭后抛未捕获异常。
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def pipeline(self) -> Pipeline:
        """暴露底层 Pipeline，用于 API 层读取默认 Config 合成请求快照。"""
        return self._pipeline

    def spawn_background(
        self,
        coro: Coroutine[object, object, None],
        *,
        name: str = "",
    ) -> asyncio.Task[None]:
        """登记一个后台任务，shutdown 时会统一 cancel + gather。

        所有 fire-and-forget 协程（DB 写入、进度广播、OCR 预热等）都
        应走这里，避免被 GC 掉或在应用关闭后仍在运行。
        """
        task = asyncio.create_task(coro, name=name or None)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def subscriber_count(self, task_id: str) -> int:
        """返回指定任务的订阅者数量（测试/诊断用）。

        说明：
        - 该方法不加锁，仅用于测试断言或诊断日志。
        - 生产逻辑请勿依赖该方法返回的强一致性。
        """
        return len(self._subscribers.get(task_id, set()))

    def create_task(
        self,
        image_dir: str,
        output_dir: str | None = None,
        llm: LLMConfig | None = None,
        ocr: OCRConfig | None = None,
        pii: PIIConfig | None = None,
    ) -> Task:
        """创建任务，状态为 PENDING。同步返回，DB 写入由后台完成。"""
        task_id = uuid.uuid4().hex[:8]
        if output_dir is None:
            output_dir = str(Path(tempfile.gettempdir()) / f"docrestore_{task_id}")
        task = Task(
            task_id=task_id,
            status=TaskStatus.PENDING,
            image_dir=image_dir,
            output_dir=output_dir,
            llm=llm,
            ocr=ocr,
            pii=pii,
        )
        self._tasks[task_id] = task

        # 异步写 DB（不阻塞创建流程）
        if self._db is not None:
            self.spawn_background(
                self._persist_new_task(task),
                name=f"persist-new-task-{task_id}",
            )

        return task

    async def _persist_new_task(self, task: Task) -> None:
        """将新任务写入 DB。"""
        if self._db is None:
            return
        try:
            await self._db.insert_task(
                task_id=task.task_id,
                status=task.status.value,
                image_dir=task.image_dir,
                output_dir=task.output_dir,
                llm=task.llm,
                ocr=task.ocr,
                pii=task.pii,
                created_at=task.created_at.isoformat(),
            )
        except Exception:
            logger.exception("持久化新任务失败: %s", task.task_id)

    async def subscribe_progress(
        self, task_id: str
    ) -> asyncio.Queue[TaskProgress] | None:
        """订阅指定任务的进度推送。

        返回值：
        - task 不存在 → None
        - task 存在 → 返回一个 `asyncio.Queue(maxsize=1)`，只保留最新进度
        """
        async with self._lock:
            if task_id not in self._tasks:
                return None
            q: asyncio.Queue[TaskProgress] = asyncio.Queue(maxsize=1)
            self._subscribers.setdefault(task_id, set()).add(q)
            return q

    async def unsubscribe_progress(
        self, task_id: str, q: asyncio.Queue[TaskProgress]
    ) -> None:
        """取消订阅进度推送，避免资源泄漏。"""
        async with self._lock:
            subs = self._subscribers.get(task_id)
            if subs is None:
                return
            subs.discard(q)
            if not subs:
                self._subscribers.pop(task_id, None)

    def publish_progress(self, task_id: str, progress: TaskProgress) -> None:
        """发布进度快照。

        说明：
        - Pipeline 的 `on_progress` 回调是同步函数，因此这里也保持同步
        - 广播投递由后台 task 完成（队列 maxsize=1，慢客户端不会产生堆积）
        """
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.progress = progress
        if self._subscribers.get(task_id):
            self.spawn_background(
                self._broadcast_progress(task_id, progress),
                name=f"broadcast-progress-{task_id}",
            )

    async def _broadcast_progress(
        self, task_id: str, progress: TaskProgress
    ) -> None:
        """向所有订阅者广播最新进度（背压：只保留最新）。"""
        async with self._lock:
            subs = list(self._subscribers.get(task_id, set()))

        for q in subs:
            with suppress(asyncio.QueueEmpty):
                q.get_nowait()

            with suppress(asyncio.QueueFull):
                q.put_nowait(progress)

    async def run_task(self, task_id: str) -> None:
        """PENDING → PROCESSING → pipeline.process_tree() → COMPLETED / FAILED"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.PROCESSING

        await self._persist_status(task_id, "processing")

        try:

            def on_progress(progress: TaskProgress) -> None:
                self.publish_progress(task_id, progress)

            # 从 scheduler 获取 GPU 锁（如有）
            gpu_lock = self._scheduler.gpu_lock if self._scheduler else None

            results = await self._pipeline.process_tree(
                image_dir=Path(task.image_dir),
                output_dir=Path(task.output_dir),
                on_progress=on_progress,
                llm=task.llm,
                gpu_lock=gpu_lock,
                pii=task.pii,
                ocr=task.ocr,
            )

            async with self._lock:
                task.status = TaskStatus.COMPLETED
                task.results = results

            await self._persist_completed(task_id, results)
            # 发送终结进度，让 WS 客户端检测到 completed 状态并退出
            self.publish_progress(
                task_id,
                TaskProgress(
                    stage="completed",
                    current=1,
                    total=1,
                    percent=100.0,
                    message="处理完成",
                ),
            )
        except asyncio.CancelledError:
            logger.info("任务 %s 被取消（应用关闭或用户取消）", task_id)
            async with self._lock:
                task.status = TaskStatus.FAILED
                task.error = "任务取消"
            try:
                await self._persist_status(
                    task_id, "failed", error="任务取消",
                )
                self.publish_progress(
                    task_id,
                    TaskProgress(
                        stage="failed",
                        current=0,
                        total=0,
                        percent=0.0,
                        message="任务取消",
                    ),
                )
            except Exception:  # noqa: BLE001 — 关闭阶段不阻断
                logger.debug("取消后清理未完成", exc_info=True)
        except Exception as exc:
            # 错误摘要（客户端可见）：类型名 + 截断消息，不含路径/堆栈
            error_summary = f"{type(exc).__name__}: {str(exc)[:200]}"
            # 服务端日志始终完整记录
            logger.exception("任务 %s 处理失败", task_id)

            async with self._lock:
                task.status = TaskStatus.FAILED
                task.error = error_summary

            await self._persist_status(task_id, "failed", error=error_summary)

            # debug 模式：完整 traceback 落盘到 output_dir/debug/error.txt
            if self._pipeline.config.debug:
                full_tb = traceback.format_exc()
                await asyncio.to_thread(
                    _write_debug_error, Path(task.output_dir), full_tb,
                )
            self.publish_progress(
                task_id,
                TaskProgress(
                    stage="failed",
                    current=0,
                    total=0,
                    percent=0.0,
                    message="处理失败",
                ),
            )
        finally:
            self._running_tasks.pop(task_id, None)

    async def _persist_status(
        self,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """将状态变更写入 DB。"""
        if self._db is None:
            return
        try:
            await self._db.update_status(task_id, status, error=error)
        except Exception:
            logger.exception("持久化状态变更失败: %s → %s", task_id, status)

    async def _persist_completed(
        self,
        task_id: str,
        results: list[PipelineResult],
    ) -> None:
        """将完成状态和结果写入 DB。"""
        if self._db is None:
            return
        try:
            await self._db.update_status(task_id, "completed")
            if results:
                rows = [
                    (str(r.output_path), r.doc_title, r.doc_dir)
                    for r in results
                ]
                await self._db.insert_results(task_id, rows)
        except Exception:
            logger.exception("持久化完成结果失败: %s", task_id)

    def get_task(self, task_id: str) -> Task | None:
        """查询任务状态（仅内存，同步接口保持兼容）。"""
        return self._tasks.get(task_id)

    async def get_task_async(self, task_id: str) -> Task | None:
        """查询任务状态：内存优先，fallback DB。"""
        task = self._tasks.get(task_id)
        if task is not None:
            return task

        if self._db is None:
            return None

        try:
            row = await self._db.get_task(task_id)
        except Exception:
            logger.exception("从 DB 查询任务失败: %s", task_id)
            return None

        if row is None:
            return None

        # 从 DB 重建 Task（不含运行时 progress）
        return Task(
            task_id=row.task_id,
            status=TaskStatus(row.status),
            image_dir=row.image_dir,
            output_dir=row.output_dir,
            llm=row.llm,
            ocr=row.ocr,
            pii=row.pii,
            error=row.error,
            created_at=datetime.fromisoformat(row.created_at),
        )

    async def list_tasks(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> TaskListResult:
        """分页查询任务列表。无 DB 时只返回内存中的任务。"""
        if self._db is not None:
            return await self._db.list_tasks(
                status=status, page=page, page_size=page_size,
            )

        all_tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        if status is not None:
            all_tasks = [t for t in all_tasks if t.status.value == status]
        total = len(all_tasks)
        start = (page - 1) * page_size
        items = [
            TaskListItem(
                task_id=t.task_id,
                status=t.status.value,
                image_dir=t.image_dir,
                output_dir=t.output_dir,
                error=t.error,
                created_at=t.created_at.isoformat(),
                result_count=len(t.results),
            )
            for t in all_tasks[start : start + page_size]
        ]
        return TaskListResult(
            tasks=items, total=total, page=page, page_size=page_size,
        )

    async def update_result_markdown(
        self,
        task_id: str,
        result_index: int,
        markdown: str,
    ) -> str | None:
        """更新指定文档的 Markdown 内容并写回磁盘。

        返回 None 表示成功，返回字符串表示错误信息。
        """
        task = self.get_task(task_id)
        if task is None:
            return "任务不存在"

        if task.status != TaskStatus.COMPLETED:
            return "任务未完成，无法编辑"

        if result_index < 0 or result_index >= len(task.results):
            return "文档索引越界"

        result = task.results[result_index]

        # 写回磁盘
        import aiofiles

        async with aiofiles.open(result.output_path, "w", encoding="utf-8") as f:
            await f.write(markdown)

        # 更新内存
        result.markdown = markdown

        return None

    # ── 任务管理操作 ────────────────────────────────────

    def register_running_task(
        self, task_id: str, bg: asyncio.Task[None],
    ) -> None:
        """注册后台运行的 asyncio.Task 引用（供取消使用）。"""
        self._running_tasks[task_id] = bg

    async def shutdown(self) -> None:
        """服务关闭时调用：cancel 所有运行中任务和后台协程并等待退出。

        必要性：Pipeline.shutdown 会释放 OCR 引擎 stdin/stdout；若此时仍有
        task 协程在 _send_command 里读写 stream，会与 engine.shutdown 并发
        抢占导致协议错乱/阻塞。先在这里统一 cancel + gather 保证串行。

        除了用户任务（_running_tasks），还要清理 fire-and-forget 的后台协程
        （DB 写入、进度广播、OCR 预热等），否则 event loop 关闭后它们会抛
        "got Future <...> attached to a different loop" 或残留运行。
        """
        running = list(self._running_tasks.values())
        background = list(self._background_tasks)
        if not running and not background:
            return

        if running:
            logger.info(
                "TaskManager.shutdown 取消 %d 个运行中任务", len(running),
            )
        if background:
            logger.info(
                "TaskManager.shutdown 取消 %d 个后台协程", len(background),
            )

        for bg in running:
            bg.cancel()
        for bg in background:
            bg.cancel()

        all_tasks = running + background
        results = await asyncio.gather(*all_tasks, return_exceptions=True)
        for bg, result in zip(all_tasks, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError,
            ):
                logger.warning(
                    "shutdown 期间任务 %s 抛出非预期异常",
                    bg.get_name(),
                    exc_info=result,
                )

        self._running_tasks.clear()
        self._background_tasks.clear()

    async def cancel_task(self, task_id: str) -> str | None:
        """取消运行中的任务。

        返回值：
        - None：任务不存在
        - 错误信息字符串：任务不可取消
        - 空字符串：取消成功
        """
        task = await self.get_task_async(task_id)
        if task is None:
            return None

        if task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
            return f"任务状态为 {task.status.value}，无法取消"

        # 取消 asyncio.Task
        bg = self._running_tasks.pop(task_id, None)
        if bg is not None:
            bg.cancel()

        # 立即更新内存状态（CancelledError handler 可能还没执行）
        async with self._lock:
            task.status = TaskStatus.FAILED
            task.error = "用户取消"

        await self._persist_status(task_id, "failed", error="用户取消")
        return ""

    async def delete_task(self, task_id: str) -> str | None:
        """删除任务及其产物。

        返回值：
        - None：任务不存在
        - 错误信息字符串：任务不可删除
        - 空字符串：删除成功
        """
        import shutil

        task = await self.get_task_async(task_id)
        if task is None:
            return None

        if task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING):
            return "任务运行中，请先取消"

        # 清理文件（快速本地 IO，无需异步化）
        output_dir = Path(task.output_dir)
        if output_dir.exists():  # noqa: ASYNC240
            shutil.rmtree(output_dir, ignore_errors=True)

        # 从内存移除
        self._tasks.pop(task_id, None)

        # 从 DB 移除
        if self._db is not None:
            try:
                await self._db.delete_task(task_id)
            except Exception:
                logger.exception("从 DB 删除任务失败: %s", task_id)

        return ""

    async def _collect_cleanup_targets(
        self,
        statuses: list[str],
    ) -> list[str]:
        """收集待清理的 task_id。内存 + DB 合并去重排序。"""
        target_ids: set[str] = set()
        async with self._lock:
            target_ids.update(
                task.task_id
                for task in self._tasks.values()
                if task.status.value in statuses
            )

        if self._db is not None:
            # 分页拿全量（page_size 上限较大，一次尽量拿多）
            for status in statuses:
                page = 1
                while True:
                    result = await self._db.list_tasks(
                        status=status, page=page, page_size=200,
                    )
                    target_ids.update(t.task_id for t in result.tasks)
                    if len(result.tasks) < 200:
                        break
                    page += 1
        return sorted(target_ids)

    async def cleanup_tasks(
        self,
        statuses: list[str],
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """批量清理指定状态的任务及其产物。

        参数：
        - statuses：需要清理的任务状态，仅允许 "completed" / "failed"

        返回：
        - (已删除的 task_id 列表, [(失败的 task_id, 原因)] 列表)

        允许状态外的 status 会被直接忽略（不抛异常），
        调用方应在更外层（API 路由）做入参校验。
        """
        allowed = {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}
        normalized = [s for s in statuses if s in allowed]
        if not normalized:
            return [], []

        target_ids = await self._collect_cleanup_targets(normalized)

        deleted: list[str] = []
        errors: list[tuple[str, str]] = []
        for tid in target_ids:
            try:
                err = await self.delete_task(tid)
            except Exception as exc:  # noqa: BLE001
                errors.append((tid, str(exc)))
                continue
            if err is None:
                errors.append((tid, "任务不存在"))
            elif err:
                errors.append((tid, err))
            else:
                deleted.append(tid)
        return deleted, errors

    async def retry_task(self, task_id: str) -> Task | str | None:
        """重试失败的任务，返回新任务。

        返回值：
        - None：原任务不存在
        - 错误信息字符串：原任务不可重试
        - Task：新创建的任务
        """
        task = await self.get_task_async(task_id)
        if task is None:
            return None

        if task.status != TaskStatus.FAILED:
            return f"任务状态为 {task.status.value}，仅失败任务可重试"

        # 用原任务配置创建新任务
        return self.create_task(
            image_dir=task.image_dir,
            llm=task.llm,
            ocr=task.ocr,
            pii=task.pii,
        )
