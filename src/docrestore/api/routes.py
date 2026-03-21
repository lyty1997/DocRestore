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

"""REST/WS 路由定义"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import FileResponse, Response
from starlette.websockets import WebSocketDisconnect

from docrestore.api.schemas import (
    CreateTaskRequest,
    ProgressResponse,
    TaskResponse,
    TaskResultResponse,
)
from docrestore.models import TaskProgress

if TYPE_CHECKING:
    from docrestore.pipeline.task_manager import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter()

# 由 app.py 在 lifespan 中注入
_task_manager: TaskManager | None = None


def set_task_manager(manager: TaskManager | None) -> None:
    """注入 TaskManager 实例。

    测试中允许传入 None 以清理全局状态。
    """
    global _task_manager  # noqa: PLW0603
    _task_manager = manager


def _get_manager() -> TaskManager:
    """获取 TaskManager，未初始化时报 500"""
    if _task_manager is None:
        raise HTTPException(status_code=500, detail="服务未初始化")
    return _task_manager


def _serialize_progress(progress: TaskProgress) -> dict[str, object]:
    """将 TaskProgress 序列化为 JSON dict。"""
    return {
        "stage": progress.stage,
        "current": progress.current,
        "total": progress.total,
        "percent": progress.percent,
        "message": progress.message,
    }


def _validate_asset_path(asset_path: str) -> PurePosixPath | None:
    """校验 assets 相对路径（MVP 仅允许 document.md 与 images/**）。"""
    if not asset_path:
        return None

    p = PurePosixPath(asset_path)

    # 禁止绝对路径与路径穿越
    if p.is_absolute() or ".." in p.parts or "." in p.parts:
        return None

    # 白名单：document.md
    if p == PurePosixPath("document.md"):
        return p

    # 白名单：images/**
    if p.parts and p.parts[0] == "images":
        return p

    return None


def _resolve_asset_path(output_dir: Path, rel_path: PurePosixPath) -> Path | None:
    """将相对路径解析到 output_dir 下，并确保不越界（含软链接穿越防护）。"""
    try:
        root = output_dir.resolve(strict=False)
        target = (output_dir / Path(*rel_path.parts)).resolve(strict=False)
    except Exception:
        return None

    if not target.is_relative_to(root):
        return None

    return target


def _build_result_zip_bytes(output_dir: Path) -> bytes:
    """打包 output_dir 下的 document.md 与 images/ 为 zip 字节。"""
    doc_path = output_dir / "document.md"
    doc_bytes = doc_path.read_bytes()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("document.md", doc_bytes)

        images_dir = output_dir / "images"
        if images_dir.exists():
            for p in sorted(images_dir.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(output_dir)
                    zf.write(p, arcname=rel.as_posix())

    return buf.getvalue()


@router.websocket("/tasks/{task_id}/progress")
async def ws_task_progress(task_id: str, websocket: WebSocket) -> None:
    """WebSocket：实时推送任务进度（AGE-12）。"""
    await websocket.accept()

    try:
        manager = _get_manager()
    except HTTPException:
        await websocket.close(code=1011)
        return

    task = manager.get_task(task_id)
    if task is None:
        await websocket.close(code=1008)
        return

    q = await manager.subscribe_progress(task_id)
    if q is None:
        await websocket.close(code=1008)
        return

    try:
        if task.progress is not None:
            await websocket.send_json(
                _serialize_progress(task.progress)
            )
        else:
            await websocket.send_json(
                _serialize_progress(
                    TaskProgress(stage="ocr", message="等待开始")
                )
            )

        if task.status.value in ("completed", "failed"):
            await websocket.close()
            return

        while True:
            progress = await q.get()
            await websocket.send_json(
                _serialize_progress(progress)
            )

            current_task = manager.get_task(task_id)
            if (
                current_task is not None
                and current_task.status.value in ("completed", "failed")
            ):
                await websocket.close()
                return
    except WebSocketDisconnect:
        return
    finally:
        with suppress(Exception):
            await manager.unsubscribe_progress(task_id, q)


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


@router.get("/tasks/{task_id}/result", response_model=TaskResultResponse)
async def get_result(task_id: str) -> TaskResultResponse:
    """获取已完成任务的结果"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

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


@router.get("/tasks/{task_id}/assets/{asset_path:path}")
async def get_task_asset(task_id: str, asset_path: str) -> FileResponse:
    """受限访问任务输出资源（AGE-13）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    rel = _validate_asset_path(asset_path)
    if rel is None:
        raise HTTPException(status_code=404, detail="资源不存在")

    target = _resolve_asset_path(Path(task.output_dir), rel)
    if target is None or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")

    # 输出目录内资源，无需额外 filename 处理
    return FileResponse(path=target)


@router.get("/tasks/{task_id}/download")
async def download_task_result(task_id: str) -> Response:
    """下载任务结果 zip（AGE-13）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    output_dir = Path(task.output_dir)
    doc_path = output_dir / "document.md"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="任务尚未完成或已失败")

    zip_bytes = _build_result_zip_bytes(output_dir)
    filename = f"docrestore_{task_id}.zip"

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )
