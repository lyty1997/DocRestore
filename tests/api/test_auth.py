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

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

import docrestore.api.auth as auth_module
from docrestore.api.auth import (
    configure_auth,
    configure_auth_from_env,
    current_token_source,
    enforce_bind_safety,
    is_auth_required,
    require_auth,
)
from docrestore.api.errors import ApiBusinessError, api_business_error_handler
from docrestore.api.routes import health_router

# 测试用 token
_TEST_TOKEN = "test-secret-token-abc123"  # noqa: S105


def _make_app(*, with_auth: bool = True) -> FastAPI:
    """构建带认证依赖的最小 FastAPI 应用。"""
    app = FastAPI()
    app.add_exception_handler(
        ApiBusinessError, api_business_error_handler,  # type: ignore[arg-type]
    )

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


@pytest.fixture(autouse=True)
def _restore_auth_globals() -> Iterator[None]:
    """快照并还原 auth 模块级 _API_TOKEN / _INSECURE_MODE（#66）。

    configure_auth* 直接改这两个模块级全局，而 _isolate_env 只还原 env、不还原
    全局 → 测试顺序相关 flaky（前一个测试设的 token 漏给后一个）。本 autouse
    fixture 模块级生效，保证每个测试前后全局状态一致。
    """
    saved_token = auth_module._API_TOKEN
    saved_insecure = auth_module._INSECURE_MODE
    saved_source = auth_module._TOKEN_SOURCE
    yield
    auth_module._API_TOKEN = saved_token
    auth_module._INSECURE_MODE = saved_insecure
    auth_module._TOKEN_SOURCE = saved_source


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
        """401 响应体应包含结构化错误信息（{code, detail, params}）。"""
        resp = await auth_client.get("/protected")
        body = resp.json()
        # ApiBusinessError 处理器：code 在顶层，detail 是中文 fallback
        assert body["code"] == "UNAUTHORIZED"
        assert isinstance(body["detail"], str)
        assert body["detail"]
        assert "params" in body


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


class TestHealthz:
    """存活探针 /healthz 不受鉴权约束（启动脚本就绪探测用）。"""

    @pytest.mark.asyncio
    async def test_healthz_open_even_with_token(self) -> None:
        """即便配置了 API token，/healthz 也应免鉴权返回 200。

        回归保护：start.sh 就绪探测原本打鉴权端点 /ocr/status，被 fail-closed
        401 刷屏且误判超时；改打此免鉴权端点。若有人误把 health_router 挂上
        require_auth，本用例会变红。
        """
        configure_auth(_TEST_TOKEN)  # 全局开启鉴权
        app = FastAPI()
        app.add_exception_handler(
            ApiBusinessError, api_business_error_handler,  # type: ignore[arg-type]
        )
        app.include_router(health_router, prefix="/api/v1")
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/v1/healthz")  # 不带任何 token
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestAuthInfo:
    """GET /auth/info：免鉴权暴露「是否需要 token + token 来源」，绝不含 token 值。"""

    @pytest.fixture
    async def info_client(self) -> AsyncIterator[AsyncClient]:
        """挂 health_router 的最小客户端（与 /healthz 同 router，免鉴权）。"""
        app = FastAPI()
        app.add_exception_handler(
            ApiBusinessError, api_business_error_handler,  # type: ignore[arg-type]
        )
        app.include_router(health_router, prefix="/api/v1")
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
        ) as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_open_without_token_and_hides_value(
        self, info_client: AsyncClient,
    ) -> None:
        """配置了 token 也免鉴权可读；响应体绝不含 token 明文。"""
        secret = "device-secret-xyz-123"  # noqa: S105 — 测试用假 token
        configure_auth(secret, source="device_file")
        resp = await info_client.get("/api/v1/auth/info")  # 不带 token
        assert resp.status_code == 200
        body = resp.json()
        expected_source = "device_file"
        assert body["auth_required"] is True
        assert body["token_source"] == expected_source
        assert secret not in resp.text  # 不泄露 token 值

    @pytest.mark.asyncio
    async def test_insecure_mode_reports_not_required(
        self, info_client: AsyncClient,
    ) -> None:
        """insecure 无鉴权模式：auth_required=False（前端据此不提示设 token）。"""
        configure_auth("")
        resp = await info_client.get("/api/v1/auth/info")
        assert resp.status_code == 200
        body = resp.json()
        expected_source = "insecure"
        assert body["auth_required"] is False
        assert body["token_source"] == expected_source


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


class TestTokenResolution:
    """configure_auth_from_env 三种 token 来源解析（fail-closed）。"""

    @pytest.fixture(autouse=True)
    def _isolate_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """隔离配置目录与鉴权环境变量，避免污染真实环境/相互干扰。"""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("DOCRESTORE_API_TOKEN", raising=False)
        monkeypatch.delenv("DOCRESTORE_ALLOW_INSECURE", raising=False)
        monkeypatch.delenv("DOCRESTORE_BIND_HOST", raising=False)

    def test_explicit_token_takes_priority(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """显式 DOCRESTORE_API_TOKEN 非空 → 直接使用，不生成 device token。"""
        explicit = "explicit-abc-123"
        monkeypatch.setenv("DOCRESTORE_API_TOKEN", explicit)
        configure_auth_from_env()
        assert auth_module._API_TOKEN == explicit
        assert auth_module._INSECURE_MODE is False
        assert current_token_source() == "env"

    def test_insecure_opt_in_disables_auth(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DOCRESTORE_ALLOW_INSECURE 为真 → 无鉴权模式（token 空）。"""
        monkeypatch.setenv("DOCRESTORE_ALLOW_INSECURE", "1")
        configure_auth_from_env()
        assert auth_module._API_TOKEN == ""
        assert auth_module._INSECURE_MODE is True
        assert current_token_source() == "insecure"
        assert is_auth_required() is False

    def test_default_generates_persistent_token(self, tmp_path: Path) -> None:
        """默认（无 token、无 insecure）→ 自动生成并落地 device token。"""
        configure_auth_from_env()
        generated = auth_module._API_TOKEN
        assert generated  # 非空，fail-closed
        assert auth_module._INSECURE_MODE is False
        assert current_token_source() == "device_file"

        token_file = tmp_path / "docrestore" / "device_token"
        assert token_file.is_file()
        assert token_file.read_text(encoding="utf-8").strip() == generated

    def test_default_token_reused_across_restart(self) -> None:
        """二次解析复用已落地 token（重启后配对不失效）。"""
        configure_auth_from_env()
        first = auth_module._API_TOKEN
        configure_auth_from_env()
        assert auth_module._API_TOKEN == first

    @pytest.mark.skipif(os.name == "nt", reason="POSIX 权限语义")
    def test_default_token_file_is_owner_only(self, tmp_path: Path) -> None:
        """落地的 device token 文件权限应为 0600（仅 owner 读写）。"""
        configure_auth_from_env()
        token_file = tmp_path / "docrestore" / "device_token"
        assert oct(token_file.stat().st_mode)[-3:] == "600"


class TestBindSafety:
    """enforce_bind_safety fail-closed bind 守卫。"""

    def test_insecure_non_loopback_refuses_start(self) -> None:
        """无鉴权模式 + 非环回绑定 → 拒绝启动。"""
        configure_auth("")  # 进入无鉴权模式
        with pytest.raises(RuntimeError, match="非环回"):
            enforce_bind_safety("0.0.0.0")  # noqa: S104 — 测试故意绑全网验证拒启

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_insecure_loopback_allowed(self, host: str) -> None:
        """无鉴权模式 + 环回绑定 → 放行。"""
        configure_auth("")
        enforce_bind_safety(host)  # 不抛异常即通过

    def test_insecure_unknown_host_refuses_start(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """无鉴权模式 + 无法判定绑定地址 → fail-closed 拒绝启动（#62）。"""
        configure_auth("")
        monkeypatch.delenv("DOCRESTORE_BIND_HOST", raising=False)
        with pytest.raises(RuntimeError, match="无法确认绑定地址"):
            enforce_bind_safety(None)

    def test_token_present_allows_any_host(self) -> None:
        """有 token → 绑任意地址都安全，不拦截。"""
        configure_auth("some-device-value")
        enforce_bind_safety("0.0.0.0")  # noqa: S104 — 有 token 任意地址都安全
