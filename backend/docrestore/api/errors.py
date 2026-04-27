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

"""API 业务错误码与统一异常类型。

设计目标：让前端可以按机器可读的 ``code`` 走 i18n 翻译，而不是把后端
的中文 ``detail`` 字符串直接展示给最终用户（之前的痛点：detail 中文
绕过 i18n、英文版 UI 也会显示中文错误）。

兼容策略：

- ``ApiBusinessError`` 继承 ``HTTPException`` —— FastAPI 的默认处理器
  仍能 fallback；自定义处理器 ``api_business_error_handler`` 把响应体
  从 ``{"detail": "..."}`` 升级为 ``{"code": "...", "detail": "...",
  "params": {...}}``。
- 中文 ``detail`` 保留作为日志/调试可读文本；前端如果识别 ``code`` 就
  按 i18n key 翻译，未识别（旧客户端 / 第三方调用）则继续显示 detail。
- ``params`` 用于参数化错误（路径、原因、上限值等），前端 i18n 模板
  ``{name}`` 占位与之对应。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse


class APIErrorCode(StrEnum):
    """业务错误码（前端 i18n 字典 key 取此值的小写形式 + ``errors.`` 前缀）。

    新增错误码时必须同步更新 ``frontend/src/i18n/zh-CN.ts`` 的
    ``errors.<code_lowercase>`` 条目（其他 locale 因 ``Record<TranslationKey,
    string>`` 严格匹配会自动报编译错）。
    """

    # ── 通用 ─────────────────────────────────────────
    UNAUTHORIZED = "UNAUTHORIZED"
    SERVICE_NOT_INITIALIZED = "SERVICE_NOT_INITIALIZED"
    ENGINE_MANAGER_NOT_INITIALIZED = "ENGINE_MANAGER_NOT_INITIALIZED"

    # ── 任务存在性 / 状态 ─────────────────────────────
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_RESULT_NOT_READY = "TASK_RESULT_NOT_READY"
    TASK_NO_RESULTS = "TASK_NO_RESULTS"
    TASK_ACTION_CONFLICT = "TASK_ACTION_CONFLICT"

    # ── 任务资源（asset / file / image / files-index）─
    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    IMAGE_NOT_FOUND = "IMAGE_NOT_FOUND"
    CODE_DIR_NOT_FOUND = "CODE_DIR_NOT_FOUND"
    FILES_INDEX_NOT_FOUND = "FILES_INDEX_NOT_FOUND"
    FILES_INDEX_PARSE_ERROR = "FILES_INDEX_PARSE_ERROR"
    FILES_INDEX_BAD_FORMAT = "FILES_INDEX_BAD_FORMAT"
    READ_FAILED = "READ_FAILED"
    INVALID_FILENAME = "INVALID_FILENAME"
    MARKDOWN_UPDATE_FAILED = "MARKDOWN_UPDATE_FAILED"

    # ── 上传会话 ─────────────────────────────────────
    UPLOAD_SESSION_NOT_FOUND = "UPLOAD_SESSION_NOT_FOUND"
    UPLOAD_SESSION_COMPLETED = "UPLOAD_SESSION_COMPLETED"
    UPLOAD_SESSION_NO_FILES = "UPLOAD_SESSION_NO_FILES"
    UPLOAD_FILE_NOT_FOUND = "UPLOAD_FILE_NOT_FOUND"

    # ── 批量清理 ─────────────────────────────────────
    CLEANUP_STATUSES_EMPTY = "CLEANUP_STATUSES_EMPTY"
    CLEANUP_STATUSES_INVALID = "CLEANUP_STATUSES_INVALID"

    # ── 服务器源 stage ───────────────────────────────
    STAGE_PATHS_EMPTY = "STAGE_PATHS_EMPTY"
    STAGE_TOO_MANY_FILES = "STAGE_TOO_MANY_FILES"
    STAGE_PATH_NOT_ABSOLUTE = "STAGE_PATH_NOT_ABSOLUTE"
    STAGE_PATH_UNRESOLVABLE = "STAGE_PATH_UNRESOLVABLE"
    STAGE_PATH_NOT_FILE = "STAGE_PATH_NOT_FILE"
    STAGE_PATH_BAD_EXT = "STAGE_PATH_BAD_EXT"
    STAGE_SYMLINK_FAILED = "STAGE_SYMLINK_FAILED"

    # ── 文件系统浏览 ─────────────────────────────────
    BROWSE_NOT_DIR = "BROWSE_NOT_DIR"
    BROWSE_PERMISSION_DENIED = "BROWSE_PERMISSION_DENIED"


class ApiBusinessError(HTTPException):
    """业务异常：携带机器可读的 ``code`` + 可参数化 ``params``。

    与 ``HTTPException`` 的兼容点：
    - ``status_code`` / ``detail`` 字段保留语义，FastAPI 默认处理器仍可用
    - 自定义处理器优先匹配，把响应体扩成 ``{code, detail, params}``
    """

    def __init__(
        self,
        code: APIErrorCode,
        status_code: int,
        detail: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code, detail=detail, headers=headers,
        )
        self.code = code
        self.params: dict[str, Any] = params or {}


async def api_business_error_handler(
    request: Request,  # noqa: ARG001 — handler 签名要求
    exc: ApiBusinessError,
) -> JSONResponse:
    """把 ``ApiBusinessError`` 序列化为 ``{code, detail, params}``。"""
    body: dict[str, Any] = {
        "code": exc.code.value,
        "detail": exc.detail,
        "params": exc.params,
    }
    headers = dict(exc.headers) if exc.headers else None
    return JSONResponse(
        status_code=exc.status_code, content=body, headers=headers,
    )
