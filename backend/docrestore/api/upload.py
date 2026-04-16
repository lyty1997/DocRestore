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

"""文件上传路由：会话管理 + 文件写入 + 清理"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from docrestore.api.schemas import (
    UploadCompleteResponse,
    UploadFilesResponse,
    UploadFileItem,
    UploadSessionFileDeleteResponse,
    UploadSessionFilesResponse,
    UploadSessionResponse,
)

logger = logging.getLogger(__name__)

upload_router = APIRouter()

# 允许的图片扩展名
_ALLOWED_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif",
})

# 默认配置
_MAX_FILE_SIZE_MB = 50
_SESSION_TTL_SECONDS = 3600  # 1 小时
_CLEANUP_INTERVAL_SECONDS = 1800  # 过期上传会话清理轮询间隔（半小时）


@dataclass
class UploadedFileRecord:
    """上传会话中的单文件记录"""

    file_id: str
    filename: str
    relative_path: str
    size_bytes: int
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class UploadSession:
    """上传会话"""

    session_id: str
    upload_dir: Path
    file_count: int = 0
    total_bytes: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    completed: bool = False
    files: dict[str, UploadedFileRecord] = field(default_factory=dict)


# 内存管理（不持久化）
_sessions: dict[str, UploadSession] = {}


def _secure_filename(name: str) -> str:
    """安全化文件名：只保留字母数字和常见字符。"""
    # 移除路径分隔符和危险字符
    cleaned = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # 只保留基本可打印字符
    result = "".join(
        c for c in cleaned
        if c.isalnum() or c in (".", "-", "_", " ")
    )
    return result.strip() or "unnamed"


def _validate_extension(filename: str) -> str | None:
    """校验文件扩展名，返回小写扩展名或 None。"""
    suffix = Path(filename).suffix.lower()
    if suffix in _ALLOWED_EXTENSIONS:
        return suffix
    return None


@upload_router.post("/uploads", response_model=UploadSessionResponse)
async def create_upload_session() -> UploadSessionResponse:
    """创建上传会话，分配临时目录。"""
    session_id = f"upl_{uuid.uuid4().hex[:8]}"
    upload_dir = Path(tempfile.mkdtemp(prefix="docrestore_upl_"))

    session = UploadSession(
        session_id=session_id,
        upload_dir=upload_dir,
    )
    _sessions[session_id] = session

    logger.info("创建上传会话: %s → %s", session_id, upload_dir)
    return UploadSessionResponse(
        session_id=session_id,
        max_file_size_mb=_MAX_FILE_SIZE_MB,
        allowed_extensions=sorted(_ALLOWED_EXTENSIONS),
    )


def _secure_relpath(raw_path: str) -> str | None:
    """安全化相对路径：校验无路径穿越，返回清洗后的相对路径。

    返回 None 表示路径不合法（包含 .. 或绝对路径）。
    """
    # 统一分隔符
    normalized = raw_path.replace("\\", "/")

    # 拒绝绝对路径和路径穿越
    if normalized.startswith("/") or ".." in normalized.split("/"):
        return None

    # 对每段路径做安全化
    parts = normalized.split("/")
    safe_parts = [_secure_filename(p) for p in parts if p]
    if not safe_parts:
        return None

    return "/".join(safe_parts)


def _resolve_upload_target(
    session: UploadSession,
    filename: str,
    paths: list[str] | None,
    idx: int,
) -> tuple[Path, str]:
    """确定文件的保存路径：优先使用 paths 中的相对路径。"""
    rel_path: str | None = None
    if paths is not None and idx < len(paths) and paths[idx]:
        rel_path = _secure_relpath(paths[idx])

    if rel_path is None:
        rel_path = _secure_filename(filename)
    return session.upload_dir / rel_path, rel_path


def _cleanup_empty_parents(root: Path, path: Path) -> None:
    """删除文件后清理 root 之下的空目录。"""
    current = path.parent
    while current != root and current.is_relative_to(root):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _build_upload_file_item(
    session_id: str,
    record: UploadedFileRecord,
) -> UploadFileItem:
    """将上传文件记录转换为响应模型。"""
    return UploadFileItem(
        session_id=session_id,
        file_id=record.file_id,
        filename=record.filename,
        relative_path=record.relative_path,
        size_bytes=record.size_bytes,
        created_at=record.created_at.isoformat(),
    )


async def _save_uploaded_file(
    file: UploadFile, target: Path, filename: str,
) -> int | None:
    """流式写入上传文件。返回写入字节数，失败返回 None。"""
    try:
        size = 0
        too_large = False
        async with aiofiles.open(target, "wb") as f:
            while True:
                chunk = await file.read(64 * 1024)  # 64KB chunks
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_FILE_SIZE_MB * 1024 * 1024:
                    too_large = True
                    break
                await f.write(chunk)

        if too_large:
            target.unlink(missing_ok=True)  # noqa: ASYNC240
            return None
        return size
    except Exception:
        logger.exception("上传文件失败: %s", filename)
        target.unlink(missing_ok=True)  # noqa: ASYNC240
        return None


@upload_router.post(
    "/uploads/{session_id}/files",
    response_model=UploadFilesResponse,
)
async def upload_files(
    session_id: str,
    files: list[UploadFile],
    paths: list[str] | None = Form(default=None),  # noqa: B008
) -> UploadFilesResponse:
    """上传文件到指定会话目录。

    可选 paths 字段：与 files 一一对应的相对路径（含子目录），
    用于保留目录结构。不传则所有文件平铺到会话根目录。
    """
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    if session.completed:
        raise HTTPException(status_code=400, detail="会话已完成，不可继续上传")

    uploaded: list[str] = []
    failed: list[str] = []

    for idx, file in enumerate(files):
        filename = file.filename or "unnamed"

        # 校验扩展名
        ext = _validate_extension(filename)
        if ext is None:
            failed.append(filename)
            continue

        target, relative_path = _resolve_upload_target(
            session, filename, paths, idx,
        )
        target.parent.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

        result = await _save_uploaded_file(file, target, filename)
        if result is None:
            failed.append(filename)
        else:
            session.file_count += 1
            session.total_bytes += result
            relative = str(target.relative_to(session.upload_dir))
            file_id = f"upf_{uuid.uuid4().hex[:12]}"
            session.files[file_id] = UploadedFileRecord(
                file_id=file_id,
                filename=target.name,
                relative_path=relative_path,
                size_bytes=result,
            )
            uploaded.append(relative)

    return UploadFilesResponse(
        session_id=session_id,
        uploaded=uploaded,
        total_uploaded=session.file_count,
        failed=failed,
    )


@upload_router.get(
    "/uploads/{session_id}/files",
    response_model=UploadSessionFilesResponse,
)
async def list_upload_session_files(session_id: str) -> UploadSessionFilesResponse:
    """列出上传会话中的文件，用于上传后预览。"""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    items = [
        _build_upload_file_item(session_id, record)
        for record in sorted(
            session.files.values(),
            key=lambda record: record.relative_path,
        )
    ]
    return UploadSessionFilesResponse(session_id=session_id, files=items)


@upload_router.get(
    "/uploads/{session_id}/files/{file_id}",
)
async def get_upload_session_file(session_id: str, file_id: str) -> FileResponse:
    """提供上传会话中的单个图片文件，用于上传后预览。"""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    record = session.files.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="上传文件不存在")

    target = (session.upload_dir / record.relative_path).resolve()
    if not target.is_relative_to(session.upload_dir.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="上传文件不存在")

    return FileResponse(path=target)


@upload_router.delete(
    "/uploads/{session_id}/files/{file_id}",
    response_model=UploadSessionFileDeleteResponse,
)
async def delete_upload_session_file(
    session_id: str,
    file_id: str,
) -> UploadSessionFileDeleteResponse:
    """删除上传会话中的单个文件。"""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    if session.completed:
        raise HTTPException(status_code=400, detail="会话已完成，不可删除文件")

    record = session.files.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="上传文件不存在")

    target = session.upload_dir / record.relative_path
    target.unlink(missing_ok=True)  # noqa: ASYNC240
    _cleanup_empty_parents(session.upload_dir, target)

    session.files.pop(file_id, None)
    session.file_count = len(session.files)
    session.total_bytes = sum(item.size_bytes for item in session.files.values())

    return UploadSessionFileDeleteResponse(
        session_id=session_id,
        file_id=file_id,
        remaining_count=session.file_count,
    )


@upload_router.post(
    "/uploads/{session_id}/complete",
    response_model=UploadCompleteResponse,
)
async def complete_upload(session_id: str) -> UploadCompleteResponse:
    """完成上传会话，返回可用于创建任务的 image_dir。"""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="上传会话不存在")

    if session.completed:
        raise HTTPException(status_code=400, detail="会话已完成")

    if session.file_count == 0:
        raise HTTPException(status_code=400, detail="会话中无文件")

    session.completed = True
    logger.info(
        "上传完成: %s, %d 个文件, %d 字节",
        session_id, session.file_count, session.total_bytes,
    )

    return UploadCompleteResponse(
        session_id=session_id,
        image_dir=str(session.upload_dir),
        file_count=session.file_count,
        total_size_bytes=session.total_bytes,
    )


async def cleanup_expired_sessions() -> None:
    """清理超时的上传会话（后台定期调用）。"""
    import shutil

    now = datetime.now()
    expired = [
        sid for sid, s in _sessions.items()
        if (now - s.created_at).total_seconds() > _SESSION_TTL_SECONDS
    ]
    for sid in expired:
        session = _sessions.pop(sid, None)
        if session is not None:
            shutil.rmtree(session.upload_dir, ignore_errors=True)
            logger.info("清理过期上传会话: %s", sid)


async def start_cleanup_task() -> asyncio.Task[None]:
    """启动后台清理任务。"""
    async def _loop() -> None:
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            await cleanup_expired_sessions()

    return asyncio.create_task(_loop())


def cleanup_all_sessions() -> None:
    """清理所有上传会话（app shutdown 时调用）。"""
    import shutil

    for session in _sessions.values():
        shutil.rmtree(session.upload_dir, ignore_errors=True)
    _sessions.clear()
