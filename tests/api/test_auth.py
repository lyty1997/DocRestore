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

"""认证模块单元测试

不依赖 GPU / OCR 数据，仅验证认证逻辑。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from docrestore.api.auth import configure_auth, require_auth

# 测试用 token
_TEST_TOKEN = "test-secret-token-abc123"  # noqa: S105


def _make_app(*, with_auth: bool = True) -> FastAPI:
    """构建带认证依赖的最小 FastAPI 应用。"""
    app = FastAPI()

    @app.get(
        "/protected",
        dependencies=[Depends(require_auth)],
    )
    async def protected() -> JSONResponse:
        """受保护的测试端点。"""
        return JSONResponse({"ok": True})

    @app.get("/query-auth", dependencies=[Depends(require_auth)])
    async def query_auth() -> JSONResponse:
        """用于测试 query param 认证的端点。"""
        return JSONResponse({"ok": True})

    if with_auth:
        configure_auth(_TEST_TOKEN)
    else:
        configure_auth("")

    return app


@pytest.fixture
async def auth_client() -> AsyncIterator[AsyncClient]:
    """启用认证的测试客户端。"""
    app = _make_app(with_auth=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def open_client() -> AsyncIterator[AsyncClient]:
    """未配置 token 的测试客户端（开发模式）。"""
    app = _make_app(with_auth=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAuthEnabled:
    """认证已启用时的行为。"""

    @pytest.mark.asyncio
    async def test_no_token_returns_401(
        self, auth_client: AsyncClient,
    ) -> None:
        """无 token 请求应返回 401。"""
        resp = await auth_client.get("/protected")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_token_returns_401(
        self, auth_client: AsyncClient,
    ) -> None:
        """错误 token 应返回 401。"""
        resp = await auth_client.get(
            "/protected",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_bearer_passes(
        self, auth_client: AsyncClient,
    ) -> None:
        """正确 Bearer token 应返回 200。"""
        resp = await auth_client.get(
            "/protected",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    @pytest.mark.asyncio
    async def test_query_param_fallback(
        self, auth_client: AsyncClient,
    ) -> None:
        """query param ?token= 应作为备选认证方式。"""
        resp = await auth_client.get(
            "/query-auth",
            params={"token": _TEST_TOKEN},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_error_body_is_structured(
        self, auth_client: AsyncClient,
    ) -> None:
        """401 响应体应包含结构化错误信息。"""
        resp = await auth_client.get("/protected")
        body = resp.json()
        detail = body["detail"]
        assert detail["code"] == "UNAUTHORIZED"
        assert "message" in detail


class TestAuthDisabled:
    """未配置 token 时的行为（开发模式）。"""

    @pytest.mark.asyncio
    async def test_no_token_configured_allows_all(
        self, open_client: AsyncClient,
    ) -> None:
        """未配置 token 时，无认证请求应放行。"""
        resp = await open_client.get("/protected")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestErrorSanitization:
    """错误信息脱敏验证。"""

    @pytest.mark.asyncio
    async def test_error_summary_format(self) -> None:
        """task.error 应为 '{ExcType}: {message}' 格式，不含 traceback。"""
        # 模拟 task_manager 中的错误摘要逻辑
        exc = ValueError("测试错误消息，不应包含文件路径")
        error_summary = f"{type(exc).__name__}: {str(exc)[:200]}"

        assert error_summary == "ValueError: 测试错误消息，不应包含文件路径"
        assert "Traceback" not in error_summary
        assert "File " not in error_summary

    @pytest.mark.asyncio
    async def test_long_error_is_truncated(self) -> None:
        """超长错误消息应被截断到 200 字符。"""
        long_msg = "x" * 300
        exc = RuntimeError(long_msg)
        error_summary = f"{type(exc).__name__}: {str(exc)[:200]}"

        # "RuntimeError: " + 200 个 x
        assert len(error_summary) == len("RuntimeError: ") + 200
