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

"""静态 Bearer Token 认证模块（可选）。

环境变量 ``DOCRESTORE_API_TOKEN`` 为空或未设置时，所有接口完全放行（开发模式）。
设置后，HTTP 请求需携带 ``Authorization: Bearer <token>`` 或 ``?token=<token>``。
WebSocket 仅支持 ``?token=<token>``（浏览器原生 WS API 不支持自定义 Header）。
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ── 模块级缓存 ──────────────────────────────────────────────
_API_TOKEN: str = ""
_bearer_scheme = HTTPBearer(auto_error=False)


def configure_auth(token: str) -> None:
    """应用启动时调用，设置全局 token。

    避免每次请求重复读取环境变量。
    """
    global _API_TOKEN  # noqa: PLW0603
    _API_TOKEN = token.strip()
    if _API_TOKEN:
        logger.info("API 认证已启用（静态 Bearer token）")
    else:
        logger.warning("未设置 DOCRESTORE_API_TOKEN，API 完全公开")


def _constant_time_equal(a: str, b: str) -> bool:
    """防时序攻击的字符串比较。"""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ── HTTP 路由认证 ────────────────────────────────────────────

async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(  # noqa: B008
        _bearer_scheme,
    ),
    token_query: str | None = Query(default=None, alias="token"),  # noqa: B008
) -> None:
    """HTTP 路由认证依赖。

    认证顺序：
    1. ``Authorization: Bearer <token>`` header（标准方式）
    2. ``?token=<token>`` query param（<img src> / <a href> 等无法设置 Header 的场景）

    未配置 ``DOCRESTORE_API_TOKEN`` 时完全放行。
    """
    if not _API_TOKEN:
        return  # 未配置 token，开发模式放行

    provided: str | None = None
    if credentials is not None:
        provided = credentials.credentials
    elif token_query is not None:
        provided = token_query

    if provided is None or not _constant_time_equal(provided, _API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "缺少或无效的 API Token"},
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── WebSocket 认证 ───────────────────────────────────────────

async def require_auth_ws(
    token: str | None = Query(default=None),  # noqa: B008
) -> None:
    """WebSocket 专用认证依赖。

    浏览器原生 ``WebSocket`` API 不支持自定义 Header，
    只能通过 ``?token=<token>`` query param 传递。
    """
    if not _API_TOKEN:
        return

    if token is None or not _constant_time_equal(token, _API_TOKEN):
        # FastAPI WS Depends 在 accept() 之前执行，
        # 抛出 HTTPException 会导致握手被拒绝（HTTP 403）
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "缺少或无效的 API Token"},
        )
