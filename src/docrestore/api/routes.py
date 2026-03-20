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

"""REST 路由定义"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from docrestore.api.schemas import (
    CreateTaskRequest,
    ProgressResponse,
    TaskResponse,
    TaskResultResponse,
)

if TYPE_CHECKING:
    from docrestore.pipeline.task_manager import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter()

# 由 app.py 在 lifespan 中注入
_task_manager: TaskManager | None = None


def set_task_manager(manager: TaskManager) -> None:
    """注入 TaskManager 实例"""
    global _task_manager  # noqa: PLW0603
    _task_manager = manager


def _get_manager() -> TaskManager:
    """获取 TaskManager，未初始化时报 500"""
    if _task_manager is None:
        raise HTTPException(
            status_code=500, detail="服务未初始化"
        )
    return _task_manager


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    req: CreateTaskRequest,
) -> TaskResponse:
    """创建任务，后台执行 Pipeline"""
    logger.info("收到创建任务请求: image_dir=%s", req.image_dir)
    manager = _get_manager()

    # 请求级 LLM 配置覆盖
    llm_override: dict[str, str | int] | None = None
    if req.llm is not None:
        llm_override = {
            k: v
            for k, v in req.llm.model_dump().items()
            if v is not None
        }

    task = manager.create_task(
        image_dir=req.image_dir,
        output_dir=req.output_dir,
        llm_override=llm_override,
    )
    logger.info("任务已创建: task_id=%s", task.task_id)
    asyncio.create_task(manager.run_task(task.task_id))
    logger.info("后台任务已启动，准备返回响应")
    return TaskResponse(
        task_id=task.task_id,
        status=task.status.value,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    """查询任务状态和进度"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail="任务不存在"
        )

    progress = None
    if task.progress is not None:
        progress = ProgressResponse(
            stage=task.progress.stage,
            current=task.progress.current,
            total=task.progress.total,
            percent=task.progress.percent,
            message=task.progress.message,
        )

    return TaskResponse(
        task_id=task.task_id,
        status=task.status.value,
        progress=progress,
        error=task.error,
    )


@router.get(
    "/tasks/{task_id}/result",
    response_model=TaskResultResponse,
)
async def get_result(task_id: str) -> TaskResultResponse:
    """获取已完成任务的结果"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail="任务不存在"
        )

    if task.result is None:
        raise HTTPException(
            status_code=404,
            detail="任务尚未完成或已失败",
        )

    return TaskResultResponse(
        task_id=task.task_id,
        output_path=str(task.result.output_path),
        markdown=task.result.markdown,
    )
