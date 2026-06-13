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

"""静态 Bearer Token 认证模块（fail-closed）。

鉴权基线：服务**永不以未鉴权状态对外可达**。Token 三种来源，按优先级：

1. 环境变量 ``DOCRESTORE_API_TOKEN`` 非空 → 用之。
2. 未设 token 但 ``DOCRESTORE_ALLOW_INSECURE`` 为真 → 无鉴权逃生口，仅供本机
   调试，且 bind 守卫只允许绑定环回地址。
3. 默认 → 自动生成强随机 device token，持久化到用户配置目录并复用（手机配对
   即用此 token），始终强制校验。

请求携带方式：HTTP 用 ``Authorization: Bearer <token>`` 或 ``?token=<token>``；
WebSocket 仅支持 ``?token=<token>``（浏览器原生 WS API 不支持自定义 Header）。
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import os
import secrets
import stat
from pathlib import Path

from fastapi import Depends, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from docrestore.api.errors import APIErrorCode, ApiBusinessError

logger = logging.getLogger(__name__)

# ── 环境变量名 ──────────────────────────────────────────────
_ENV_API_TOKEN = "DOCRESTORE_API_TOKEN"  # noqa: S105 — 变量名含 token 触发误报，此为环境变量名非密钥
_ENV_ALLOW_INSECURE = "DOCRESTORE_ALLOW_INSECURE"
_ENV_BIND_HOST = "DOCRESTORE_BIND_HOST"

# ── device token 持久化 ─────────────────────────────────────
_DEVICE_TOKEN_FILENAME = "device_token"  # noqa: S105 — 文件名非密钥
_DEVICE_TOKEN_NBYTES = 32

# 视为「环回」（仅本机可达）的绑定地址
_LOOPBACK_HOSTS = frozenset(
    {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"},
)

# ── 模块级状态 ──────────────────────────────────────────────
_API_TOKEN: str = ""
_INSECURE_MODE: bool = False  # 显式无鉴权（DOCRESTORE_ALLOW_INSECURE）
_bearer_scheme = HTTPBearer(auto_error=False)


def _is_truthy(value: str | None) -> bool:
    """环境变量真值判定（1/true/yes/on，大小写不敏感）。"""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _config_dir() -> Path:
    """跨平台用户配置目录。

    Windows 用 ``%APPDATA%\\docrestore``；POSIX 用 ``$XDG_CONFIG_HOME/docrestore``，
    未设 XDG_CONFIG_HOME 时退化 ``~/.config/docrestore``。
    """
    # 用中间变量读取 os.name，避免静态分析器按当前平台把 Windows 分支判成死代码
    platform_name = os.name
    if platform_name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "docrestore"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "docrestore"


def _load_or_create_device_token() -> str:
    """读取持久化 device token；不存在/为空则生成强随机 token 落地（POSIX 0600）。

    落地失败时回退内存临时 token（重启后配对失效，打 warning），保证服务仍
    fail-closed（始终有 token 强制校验）。
    """
    token_path = _config_dir() / _DEVICE_TOKEN_FILENAME
    try:
        existing = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing

    token = secrets.token_urlsafe(_DEVICE_TOKEN_NBYTES)
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token, encoding="utf-8")
        # 仅 owner 可读写（POSIX；Windows 上 chmod 语义有限，best-effort）
        with contextlib.suppress(OSError):
            token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        logger.info("已生成并落地 device token: %s", token_path)
    except OSError:
        logger.warning(
            "device token 落地失败（%s），本次使用内存临时 token，重启后需重新配对",
            token_path,
            exc_info=True,
        )
    return token


def configure_auth(token: str) -> None:
    """底层 setter：直接设置全局 token（空字符串 = 无鉴权模式）。

    供测试与 :func:`configure_auth_from_env` 复用；生产启动应走
    :func:`configure_auth_from_env`。
    """
    global _API_TOKEN, _INSECURE_MODE  # noqa: PLW0603
    _API_TOKEN = token.strip()
    _INSECURE_MODE = not _API_TOKEN
    if _API_TOKEN:
        logger.info("API 认证已启用（静态 Bearer token）")
    else:
        logger.warning("API 处于无鉴权模式，所有接口放行（仅限本机调试）")


def configure_auth_from_env() -> None:
    """启动时从环境解析鉴权配置（fail-closed），三选一。

    见模块 docstring 的优先级说明。无论走哪条分支，除显式 insecure 外都保证
    ``_API_TOKEN`` 非空（始终强制校验）。
    """
    explicit = os.environ.get(_ENV_API_TOKEN, "").strip()
    if explicit:
        configure_auth(explicit)
        return

    if _is_truthy(os.environ.get(_ENV_ALLOW_INSECURE)):
        configure_auth("")
        logger.warning(
            "%s 已开启：API 无鉴权运行，bind 守卫仅允许绑定环回地址",
            _ENV_ALLOW_INSECURE,
        )
        return

    configure_auth(_load_or_create_device_token())
    logger.info(
        "未设 %s：已自动生成 device token 并强制校验（手机配对用此 token）",
        _ENV_API_TOKEN,
    )


def _is_loopback_host(host: str) -> bool:
    """判定绑定地址是否为环回（仅本机可达）。"""
    return host.strip().lower() in _LOOPBACK_HOSTS


def enforce_bind_safety(bind_host: str | None = None) -> None:
    """fail-closed bind 守卫：无鉴权模式下禁止绑定非环回地址。

    ``bind_host`` 为 None 时从 ``DOCRESTORE_BIND_HOST`` 读取；仍无法判定则放行
    但告警。有 token 时任意地址都安全（每个请求都校验），直接返回。

    Raises:
        RuntimeError: 无鉴权模式且绑定非环回地址时拒绝启动。
    """
    if not _INSECURE_MODE:
        return  # 有 token，绑任意地址都校验，安全

    host = bind_host if bind_host is not None else os.environ.get(_ENV_BIND_HOST)
    if not host:
        logger.warning(
            "无鉴权模式但无法判定绑定地址（未设 %s）：请仅在 loopback 使用；"
            "要对外暴露请配置 %s",
            _ENV_BIND_HOST,
            _ENV_API_TOKEN,
        )
        return

    if _is_loopback_host(host):
        logger.warning("无鉴权模式：仅绑定环回地址 %s（本机可达）", host)
        return

    raise RuntimeError(
        f"拒绝启动：{_ENV_ALLOW_INSECURE} 无鉴权模式不允许绑定非环回地址 "
        f"{host!r}。要让局域网/远程设备访问，请配置 {_ENV_API_TOKEN}（推荐），"
        f"或仅绑定 127.0.0.1。",
    )


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

    无鉴权模式（``_API_TOKEN`` 为空）时放行。
    """
    if not _API_TOKEN:
        return  # 无鉴权模式放行

    provided: str | None = None
    if credentials is not None:
        provided = credentials.credentials
    elif token_query is not None:
        provided = token_query

    if provided is None or not _constant_time_equal(provided, _API_TOKEN):
        raise ApiBusinessError(
            APIErrorCode.UNAUTHORIZED,
            status.HTTP_401_UNAUTHORIZED,
            "缺少或无效的 API Token",
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
        # 抛出异常会导致握手被拒绝
        raise ApiBusinessError(
            APIErrorCode.UNAUTHORIZED,
            status.HTTP_401_UNAUTHORIZED,
            "缺少或无效的 API Token",
        )


def _constant_time_equal(a: str, b: str) -> bool:
    """防时序攻击的字符串比较。"""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
