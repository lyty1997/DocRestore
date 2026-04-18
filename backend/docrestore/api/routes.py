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
import dataclasses
import io
import logging
import zipfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, Response
from starlette.websockets import WebSocketDisconnect

from docrestore.api.auth import require_auth_ws

from docrestore.api.schemas import (
    ActionResponse,
    BrowseDirsResponse,
    CreateTaskRequest,
    CustomSensitiveWord,
    DirEntry,
    OCRStatusResponse,
    OCRWarmupRequest,
    ProgressResponse,
    SourceImagesResponse,
    StageServerSourceRequest,
    StageServerSourceResponse,
    TaskListItem,
    TaskListResponse,
    TaskResponse,
    TaskResultResponse,
    TaskResultsResponse,
    UpdateMarkdownRequest,
)
from docrestore.models import TaskProgress
from docrestore.pipeline.config import (
    CustomWord,
    LLMConfig,
    OCRConfig,
    PIIConfig,
)

if TYPE_CHECKING:
    from docrestore.ocr.engine_manager import EngineManager
    from docrestore.pipeline.task_manager import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter()
ws_router = APIRouter()  # WebSocket 路由（不挂 HTTP 认证，WS 用 require_auth_ws）

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


def _to_custom_words(
    raw: list[CustomSensitiveWord] | list[str],
) -> list[CustomWord]:
    """将 API 层敏感词列表（字符串或对象）转换为 CustomWord dataclass。

    兼容旧式纯字符串列表与新的 {word, code?} 对象列表；code 空串和 None 等价。
    """
    result: list[CustomWord] = []
    for item in raw:
        if isinstance(item, str):
            if item:
                result.append(CustomWord(word=item))
        else:
            word = item.word
            if word:
                result.append(
                    CustomWord(word=word, code=item.code or ""),
                )
    return result




def _validate_asset_path(asset_path: str) -> PurePosixPath | None:
    """校验 assets 相对路径。

    允许：document.md / images/** / {subdir}/document.md / {subdir}/images/**
    """
    if not asset_path:
        return None

    p = PurePosixPath(asset_path)

    # 禁止绝对路径与路径穿越
    if p.is_absolute() or ".." in p.parts or "." in p.parts:
        return None

    # 白名单：document.md（根目录或子目录下）
    if p.name == "document.md" and len(p.parts) <= 2:
        return p

    # 白名单：images/**（根目录或子目录下）
    if "images" in p.parts:
        idx = list(p.parts).index("images")
        # images 在根目录或第一层子目录下
        if idx <= 1:
            return p

    return None


def _resolve_asset_path(output_dir: Path, rel_path: PurePosixPath) -> Path | None:
    """将相对路径解析到 output_dir 下，并确保不越界（含软链接穿越防护）。"""
    try:
        root = output_dir.resolve(strict=False)
        target = (output_dir / Path(*rel_path.parts)).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None

    if not target.is_relative_to(root):
        return None

    return target


def _build_result_zip_bytes(output_dir: Path, doc_dirs: list[str]) -> bytes:
    """打包任务结果为 zip 字节。

    单文档（doc_dirs 为空或只有空字符串）：document.md + images/
    多文档：{doc_dir}/document.md + {doc_dir}/images/ × N
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 确定要打包的子目录列表
        dirs_to_pack = [d for d in doc_dirs if d] if doc_dirs else []

        if not dirs_to_pack:
            # 单文档：根目录
            _add_doc_to_zip(zf, output_dir, "")
        else:
            for d in dirs_to_pack:
                _add_doc_to_zip(zf, output_dir / d, d)

    return buf.getvalue()


def _add_doc_to_zip(
    zf: zipfile.ZipFile,
    doc_dir: Path,
    prefix: str,
) -> None:
    """将单个文档目录的 document.md + images/ 写入 zip。"""
    doc_path = doc_dir / "document.md"
    if doc_path.exists():
        arcname = f"{prefix}/document.md" if prefix else "document.md"
        zf.write(doc_path, arcname=arcname)

    images_dir = doc_dir / "images"
    if images_dir.exists():
        for p in sorted(images_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(doc_dir).as_posix()
                arcname = f"{prefix}/{rel}" if prefix else rel
                zf.write(p, arcname=arcname)


def _build_task_response(task_id: str) -> TaskResponse:
    """构建 TaskResponse（复用逻辑）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    progress = None
    if task.progress is not None:
        progress = ProgressResponse.model_validate(
            dataclasses.asdict(task.progress),
        )

    return TaskResponse(
        task_id=task.task_id,
        status=task.status.value,
        progress=progress,
        error=task.error,
    )


@ws_router.websocket("/tasks/{task_id}/progress")
async def ws_task_progress(
    task_id: str,
    websocket: WebSocket,
    _auth: None = Depends(require_auth_ws),
) -> None:
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
        initial = task.progress or TaskProgress(
            stage="ocr", message="等待开始",
        )
        await websocket.send_json(dataclasses.asdict(initial))

        if task.status.value in ("completed", "failed"):
            await websocket.close()
            return

        while True:
            progress = await q.get()
            await websocket.send_json(dataclasses.asdict(progress))

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


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> TaskListResponse:
    """分页查询任务列表"""
    manager = _get_manager()

    # 限制 page_size 范围
    page_size = max(1, min(page_size, _PAGE_SIZE_MAX))
    page = max(1, page)

    result = await manager.list_tasks(
        status=status, page=page, page_size=page_size,
    )
    return TaskListResponse(
        tasks=[
            TaskListItem.model_validate(dataclasses.asdict(t))
            for t in result.tasks
        ],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    req: CreateTaskRequest,
) -> TaskResponse:
    """创建任务，后台执行 Pipeline。

    本路由是 API 增量字段 → 完整 Config 的唯一合成点：对每类 Config
    取 pipeline 的默认值，`model_copy(update=...)` 叠加请求中的非空字段，
    然后把完整 Config 往下游传（TaskManager / DB / Pipeline 不再做合并）。
    """
    logger.info("收到创建任务请求: image_dir=%s", req.image_dir)
    manager = _get_manager()
    defaults = manager.pipeline.config

    llm_cfg: LLMConfig | None = None
    if req.llm is not None:
        llm_cfg = defaults.llm.model_copy(
            update=req.llm.model_dump(exclude_none=True),
        )

    ocr_cfg: OCRConfig | None = None
    if req.ocr is not None:
        ocr_cfg = defaults.ocr.model_copy(
            update=req.ocr.model_dump(exclude_none=True),
        )

    pii_cfg: PIIConfig | None = None
    if req.pii is not None:
        pii_update: dict[str, object] = {}
        if req.pii.enable is not None:
            pii_update["enable"] = req.pii.enable
        if req.pii.custom_sensitive_words is not None:
            pii_update["custom_sensitive_words"] = (
                _to_custom_words(req.pii.custom_sensitive_words)
            )
        pii_cfg = defaults.pii.model_copy(update=pii_update)

    task = manager.create_task(
        image_dir=req.image_dir,
        output_dir=req.output_dir,
        llm=llm_cfg,
        ocr=ocr_cfg,
        pii=pii_cfg,
    )
    logger.info("任务已创建: task_id=%s", task.task_id)
    bg = asyncio.create_task(
        manager.run_task(task.task_id),
        name=f"run-task-{task.task_id}",
    )
    try:
        manager.register_running_task(task.task_id, bg)
    except BaseException:
        # register_running_task 抛出（极少见，例如 dict 被外部篡改）时
        # 必须 cancel bg，否则 create_task 启动的协程完全脱管
        bg.cancel()
        raise
    logger.info("后台任务已启动，准备返回响应")
    return TaskResponse(
        task_id=task.task_id,
        status=task.status.value,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    """查询任务状态和进度（含父子任务信息）"""
    return _build_task_response(task_id)


@router.get("/tasks/{task_id}/result")
async def get_result(
    task_id: str,
) -> TaskResultResponse:
    """获取已完成任务的结果。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status.value != "completed":
        raise HTTPException(
            status_code=404,
            detail="任务尚未完成或已失败",
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
        doc_title=task.result.doc_title,
        doc_dir=task.result.doc_dir,
    )


@router.get("/tasks/{task_id}/results")
async def get_results(
    task_id: str,
) -> TaskResultsResponse:
    """获取已完成任务的全部文档结果列表。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status.value != "completed" or not task.results:
        raise HTTPException(
            status_code=404,
            detail="任务尚未完成或已失败",
        )

    items = [
        TaskResultResponse(
            task_id=task.task_id,
            output_path=str(r.output_path),
            markdown=r.markdown,
            doc_title=r.doc_title,
            doc_dir=r.doc_dir,
        )
        for r in task.results
    ]
    return TaskResultsResponse(
        task_id=task.task_id,
        results=items,
    )


@router.put(
    "/tasks/{task_id}/results/{result_index}",
    response_model=ActionResponse,
)
async def update_result_markdown(
    task_id: str,
    result_index: int,
    req: UpdateMarkdownRequest,
) -> ActionResponse:
    """更新指定文档的 Markdown 内容（人工精修）。"""
    manager = _get_manager()
    error = await manager.update_result_markdown(
        task_id, result_index, req.markdown,
    )
    if error is not None:
        raise HTTPException(status_code=400, detail=error)

    return ActionResponse(task_id=task_id, message="保存成功")


@router.get("/tasks/{task_id}/assets/{asset_path:path}")
async def get_task_asset(task_id: str, asset_path: str) -> FileResponse:
    """受限访问任务输出资源（AGE-13）。

    支持父任务和子任务。子任务的 output_dir 指向聚类组目录。
    """
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

    return FileResponse(path=target)


@router.get("/tasks/{task_id}/download")
async def download_task_result(task_id: str) -> Response:
    """下载任务结果 zip（AGE-13）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    output_dir = Path(task.output_dir)

    # 收集子目录列表
    doc_dirs = [r.doc_dir for r in task.results] if task.results else []

    # 至少有一个 document.md 存在才能下载
    has_any = any(
        (output_dir / d / "document.md" if d else output_dir / "document.md").exists()
        for d in (doc_dirs or [""])
    )
    if not has_any:
        raise HTTPException(status_code=404, detail="任务尚未完成或已失败")

    zip_bytes = _build_result_zip_bytes(output_dir, doc_dirs)
    filename = f"docrestore_{task_id}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


# ── 源图片访问 ──────────────────────────────────────────

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"})


@router.get(
    "/tasks/{task_id}/source-images",
    response_model=SourceImagesResponse,
)
async def list_source_images(task_id: str) -> SourceImagesResponse:
    """列出任务的源图片文件名（按文件名排序）。"""
    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    import asyncio

    def _scan() -> list[str]:
        img_dir = Path(task.image_dir)
        if not img_dir.is_dir():
            return []
        # 递归扫描，返回相对于 image_dir 的路径
        return sorted(
            p.relative_to(img_dir).as_posix()
            for p in img_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )

    images = await asyncio.to_thread(_scan)
    return SourceImagesResponse(task_id=task_id, images=images)


@router.get("/tasks/{task_id}/source-images/{filename:path}")
async def get_source_image(task_id: str, filename: str) -> FileResponse:
    """提供单张源图片文件（含路径穿越防护）。"""
    import asyncio

    manager = _get_manager()
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 路径安全校验
    if not filename or ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="非法文件名")

    def _resolve() -> Path | None:
        """同步解析并校验图片路径。"""
        img_dir = Path(task.image_dir)
        t = (img_dir / filename).resolve()
        if not t.is_relative_to(img_dir.resolve()):
            return None
        if not t.is_file():
            return None
        if t.suffix.lower() not in _IMAGE_EXTS:
            return None
        return t

    target = await asyncio.to_thread(_resolve)

    if target is None:
        raise HTTPException(status_code=404, detail="图片不存在")

    return FileResponse(path=target)


# ── 任务管理操作 ──────────────────────────────────────


@router.post("/tasks/{task_id}/cancel", response_model=ActionResponse)
async def cancel_task(task_id: str) -> ActionResponse:
    """取消运行中的任务"""
    manager = _get_manager()
    result = await manager.cancel_task(task_id)

    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if result:
        raise HTTPException(status_code=409, detail=result)

    return ActionResponse(
        task_id=task_id,
        message="任务已取消",
    )


@router.delete("/tasks/{task_id}", response_model=ActionResponse)
async def delete_task(task_id: str) -> ActionResponse:
    """删除任务及其产物"""
    manager = _get_manager()
    result = await manager.delete_task(task_id)

    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if result:
        raise HTTPException(status_code=409, detail=result)

    return ActionResponse(
        task_id=task_id,
        message="任务及产物已删除",
    )


@router.post("/tasks/{task_id}/retry", response_model=ActionResponse)
async def retry_task(task_id: str) -> ActionResponse:
    """重试失败的任务"""
    manager = _get_manager()
    result = await manager.retry_task(task_id)

    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if isinstance(result, str):
        raise HTTPException(status_code=409, detail=result)

    # result 是新创建的 Task
    bg = asyncio.create_task(
        manager.run_task(result.task_id),
        name=f"run-task-{result.task_id}",
    )
    try:
        manager.register_running_task(result.task_id, bg)
    except BaseException:
        bg.cancel()
        raise

    return ActionResponse(
        task_id=result.task_id,
        message="已创建重试任务",
    )


# ── 文件系统浏览 ────────────────────────────────────────


_IMAGE_COUNT_CAP = 9999
_PAGE_SIZE_MAX = 100  # 任务列表分页上限（防止单次拉取过大结果）
_STAGE_FILES_MAX = 5000  # 单次服务器侧文件暂存最大数量（防止滥用/超时）


def _count_top_images(dir_path: Path) -> int | None:
    """浅扫描目录，统计顶层图片文件数；不可读返回 None。

    达到 _IMAGE_COUNT_CAP 后停止并返回该上限值（前端可展示 "9999+"）。
    """
    import os

    count = 0
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                ext = Path(entry.name).suffix.lower()
                if ext in _IMAGE_EXTS:
                    count += 1
                    if count >= _IMAGE_COUNT_CAP:
                        return _IMAGE_COUNT_CAP
    except (PermissionError, OSError):
        return None
    return count


def _build_dir_entry(child: Path, with_files: bool) -> DirEntry | None:
    """将目录项转换为 DirEntry；跳过返回 None。

    with_files=True 时目录条目额外携带 image_count（顶层图片数预览）。
    """
    try:
        if child.is_dir():
            image_count = _count_top_images(child) if with_files else None
            return DirEntry(
                name=child.name, is_dir=True, image_count=image_count,
            )
        if with_files and child.is_file():
            if child.suffix.lower() not in _IMAGE_EXTS:
                return None
            try:
                size: int | None = child.stat().st_size
            except OSError:
                size = None
            return DirEntry(name=child.name, is_dir=False, size_bytes=size)
    except PermissionError:
        return None
    return None


def _scan_dir(p: str, with_files: bool) -> BrowseDirsResponse:
    """同步扫描目录。"""
    target = Path(p).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(
            status_code=400, detail=f"路径不是目录: {target}",
        )

    try:
        children = sorted(target.iterdir(), key=lambda x: x.name.lower())
    except PermissionError:
        raise HTTPException(  # noqa: B904
            status_code=403, detail=f"无权限访问: {target}",
        )

    entries: list[DirEntry] = []
    for child in children:
        if child.name.startswith("."):
            continue
        entry = _build_dir_entry(child, with_files)
        if entry is not None:
            entries.append(entry)

    parent = str(target.parent) if target.parent != target else None
    return BrowseDirsResponse(
        path=str(target), parent=parent, entries=entries,
    )


@router.get("/filesystem/dirs", response_model=BrowseDirsResponse)
async def browse_dirs(
    path: str = "~", include_files: bool = False,
) -> BrowseDirsResponse:
    """列出指定路径下的子目录和（可选）文件，供前端来源选择器使用。

    - path 为 "~" 时展开为用户主目录
    - 默认仅列出目录；include_files=True 时额外返回 _IMAGE_EXTS 范围内的文件
    - 不可读的目录/文件跳过（不报错）
    """
    return await asyncio.to_thread(_scan_dir, path, include_files)


# ── 服务器源 stage（将已有文件聚合为 image_dir）──────────
#
# 设计：用户在服务器文件浏览器中多选一批图片后，调用本接口，
# 后端在 tempfile.mkdtemp() 目录中为每个文件创建符号链接，返回
# 临时目录路径作为 image_dir，可直接传给 create_task。
# 文件名冲突时追加数字后缀防止覆盖。


def _resolve_stage_path(raw: str) -> Path:
    """校验单个 stage 路径：绝对、可解析、普通文件、图片扩展名。"""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise HTTPException(
            status_code=400, detail=f"路径必须为绝对路径: {raw}",
        )
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(  # noqa: B904
            status_code=400, detail=f"路径无法解析: {raw} ({exc})",
        )
    if not real.is_file():
        raise HTTPException(
            status_code=400, detail=f"不是普通文件: {real}",
        )
    if real.suffix.lower() not in _IMAGE_EXTS:
        raise HTTPException(
            status_code=400, detail=f"不支持的文件类型: {real}",
        )
    return real


def _allocate_link_name(base: str, used: set[str]) -> str:
    """为 symlink 分配唯一文件名，冲突时追加 _1/_2/... 后缀。"""
    if base not in used:
        return base
    stem, ext = Path(base).stem, Path(base).suffix
    idx = 1
    while True:
        candidate = f"{stem}_{idx}{ext}"
        if candidate not in used:
            return candidate
        idx += 1


def _stage_files(raw_paths: list[str]) -> StageServerSourceResponse:
    """同步执行文件校验 + 符号链接创建。"""
    import shutil
    import tempfile

    resolved = [_resolve_stage_path(raw) for raw in raw_paths]

    stage_dir = Path(tempfile.mkdtemp(prefix="docrestore_src_"))
    used_names: set[str] = set()
    for src in resolved:
        name = _allocate_link_name(src.name, used_names)
        used_names.add(name)
        try:
            (stage_dir / name).symlink_to(src)
        except OSError as exc:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise HTTPException(  # noqa: B904
                status_code=500,
                detail=f"创建符号链接失败: {src} → {exc}",
            )

    logger.info(
        "服务器源 stage 完成: %d 个文件 → %s",
        len(resolved), stage_dir,
    )
    return StageServerSourceResponse(
        image_dir=str(stage_dir),
        file_count=len(resolved),
    )


@router.post("/sources/server", response_model=StageServerSourceResponse)
async def stage_server_source(
    req: StageServerSourceRequest,
) -> StageServerSourceResponse:
    """将服务器上已有文件 stage 为可作为 image_dir 使用的临时目录。

    - 每个路径必须绝对、存在、为普通文件、扩展名在 _IMAGE_EXTS 内
    - 服务端创建 /tmp/docrestore_src_xxx 目录，为每个文件创建符号链接
    - 返回临时目录路径，调用方使用后自行管理生命周期（不自动清理）
    """
    if not req.paths:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    if len(req.paths) > _STAGE_FILES_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {_STAGE_FILES_MAX} 个文件",
        )

    return await asyncio.to_thread(_stage_files, req.paths)


# ── OCR 引擎预热 ──────────────────────────────────────────


def _get_engine_manager(request: Request) -> EngineManager:
    """从 app.state 获取 EngineManager 实例。"""
    em: EngineManager | None = getattr(request.app.state, "engine_manager", None)
    if em is None:
        raise HTTPException(
            status_code=500,
            detail="EngineManager 未初始化",
        )
    return em


@router.get("/ocr/status", response_model=OCRStatusResponse)
async def get_ocr_status(request: Request) -> OCRStatusResponse:
    """查询当前 OCR 引擎状态。"""
    em = _get_engine_manager(request)
    return OCRStatusResponse(
        current_model=em.current_model,
        current_gpu=em.current_gpu,
        is_ready=em.is_ready,
        is_switching=em.is_switching,
    )


@router.post("/ocr/warmup")
async def warmup_ocr_engine(
    req: OCRWarmupRequest,
    request: Request,
) -> dict[str, str]:
    """预加载指定 OCR 引擎（后台异步，立即返回）。"""
    em = _get_engine_manager(request)

    # 已匹配且就绪 → 直接返回
    if em.is_ready and em.current_model == req.model and em.current_gpu == req.gpu_id:
        return {"status": "ready", "message": "引擎已就绪"}

    # 正在切换 → 返回 switching 状态
    if em.is_switching:
        return {"status": "switching", "message": "引擎正在切换中"}

    # 构造完整配置并发起后台预热
    manager = _get_manager()
    warmup_config = manager.pipeline.config.ocr.model_copy(
        update={"model": req.model, "gpu_id": req.gpu_id},
    )

    async def _do_warmup() -> None:
        """后台执行引擎预热。"""
        try:
            await em.ensure(warmup_config)
            logger.info(
                "OCR 引擎预热完成: %s (GPU %s)",
                req.model, req.gpu_id,
            )
        except asyncio.CancelledError:
            # 应用 shutdown 时 TaskManager 会 cancel 所有后台任务
            logger.info("OCR 引擎预热被取消")
            raise
        except Exception:
            logger.warning("OCR 引擎预热失败", exc_info=True)

    # 通过 TaskManager 统一追踪，shutdown 时 cancel + gather
    manager.spawn_background(_do_warmup(), name=f"ocr-warmup-{req.model}")
    return {"status": "accepted", "message": "引擎预热已开始"}
