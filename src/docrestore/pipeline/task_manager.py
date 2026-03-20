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

"""任务生命周期管理（内存存储，MVP 不持久化）"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from docrestore.models import PipelineResult, TaskProgress
from docrestore.pipeline.pipeline import Pipeline


class TaskStatus(Enum):
    """任务状态"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """任务记录"""

    task_id: str
    status: TaskStatus
    image_dir: str
    output_dir: str
    llm_override: dict[str, str | int] | None = None
    progress: TaskProgress | None = None
    result: PipelineResult | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)


class TaskManager:
    """任务生命周期管理"""

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()

    def create_task(
        self,
        image_dir: str,
        output_dir: str | None = None,
        llm_override: dict[str, str | int] | None = None,
    ) -> Task:
        """创建任务，状态为 PENDING。"""
        task_id = uuid.uuid4().hex[:8]
        if output_dir is None:
            output_dir = f"/tmp/docrestore_{task_id}"  # noqa: S108
        task = Task(
            task_id=task_id,
            status=TaskStatus.PENDING,
            image_dir=image_dir,
            output_dir=output_dir,
            llm_override=llm_override,
        )
        self._tasks[task_id] = task
        return task

    async def run_task(self, task_id: str) -> None:
        """PENDING → PROCESSING → pipeline.process() → COMPLETED / FAILED"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = TaskStatus.PROCESSING

        try:

            def on_progress(progress: TaskProgress) -> None:
                task.progress = progress

            result = await self._pipeline.process(
                image_dir=Path(task.image_dir),
                output_dir=Path(task.output_dir),
                on_progress=on_progress,
                llm_override=task.llm_override,
            )
            async with self._lock:
                task.status = TaskStatus.COMPLETED
                task.result = result
        except Exception:
            async with self._lock:
                task.status = TaskStatus.FAILED
                task.error = traceback.format_exc()

    def get_task(self, task_id: str) -> Task | None:
        """查询任务状态"""
        return self._tasks.get(task_id)
