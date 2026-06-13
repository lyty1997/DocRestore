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

"""LLM api_base 出站 SSRF 守卫单测（#33）。

策略：私网 / 环回 / 链路本地 / 元数据等用**字面量 IP**直接断言（跳过 DNS，
确定性）；主机名→私网 IP 的解析拦截路径用 mock ``socket.getaddrinfo`` 验证；
白名单逃生口用 env 注入验证。
"""

from __future__ import annotations

import socket

import pytest

import docrestore.api.url_guard as url_guard
from docrestore.api.errors import APIErrorCode, ApiBusinessError
from docrestore.api.url_guard import validate_outbound_api_base


@pytest.fixture(autouse=True)
def _clear_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认每个用例都清掉白名单 env，避免相互污染（白名单用例自行设置）。"""
    monkeypatch.delenv(url_guard._ENV_API_BASE_ALLOWLIST, raising=False)


def _expect_rejected(api_base: str) -> None:
    """断言给定 api_base 被守卫以 LLM_API_BASE_REJECTED 拒绝。"""
    with pytest.raises(ApiBusinessError) as exc_info:
        validate_outbound_api_base(api_base)
    assert exc_info.value.code == APIErrorCode.LLM_API_BASE_REJECTED
    assert exc_info.value.status_code == 400


class TestSsrfBlocking:
    """无白名单（默认）时的 SSRF 拦截：一切非公网可路由地址必拒。"""

    @pytest.mark.parametrize(
        "api_base",
        [
            "http://10.0.0.5/v1",  # RFC1918 私网
            "http://192.168.1.10/v1",  # RFC1918 私网
            "http://172.16.0.1/v1",  # RFC1918 私网
            "http://169.254.169.254/latest/meta-data",  # 云元数据端点
            "http://[::ffff:169.254.169.254]/v1",  # IPv4-mapped 元数据旁路
            "http://0.0.0.0/v1",  # 未指定地址
        ],
    )
    def test_non_public_ip_rejected(self, api_base: str) -> None:
        """私网 / 链路本地 / 元数据 / 映射旁路一律拒绝（环回除外，见下）。"""
        _expect_rejected(api_base)

    @pytest.mark.parametrize(
        "api_base",
        [
            "https://1.1.1.1/v1",  # 公网 IPv4 字面量
            "https://8.8.8.8/v1",
            "http://127.0.0.1:11434/v1",  # 环回：同机本地 LLM（ollama）
            "http://[::1]:8000/v1",  # IPv6 环回
            "http://[::ffff:127.0.0.1]/v1",  # IPv4-mapped 环回
        ],
    )
    def test_public_or_loopback_allowed(self, api_base: str) -> None:
        """公网地址 + 环回（本地 LLM 合法目标）放行，不抛异常。"""
        validate_outbound_api_base(api_base)

    def test_empty_returns_without_check(self) -> None:
        """空串 → 直接返回（用后端默认 api_base，不校验）。"""
        validate_outbound_api_base("")
        validate_outbound_api_base("   ")


class TestSchemeAndHost:
    """非 http(s) scheme 与缺主机名拒绝。"""

    @pytest.mark.parametrize(
        "api_base",
        [
            "file:///etc/passwd",
            "gopher://127.0.0.1/",
            "ftp://example.com/",
            "http:///v1",  # 缺 host
        ],
    )
    def test_bad_scheme_or_host_rejected(self, api_base: str) -> None:
        """非 http/https 或无主机名一律拒绝。"""
        _expect_rejected(api_base)


class TestHostnameResolution:
    """主机名解析路径：解析到私网 IP 拦截、解析失败拦截、公网放行。"""

    @staticmethod
    def _fake_getaddrinfo(
        ip: str,
    ) -> object:
        """构造返回固定 IP 的 getaddrinfo 替身。"""

        def _inner(
            host: str,  # noqa: ARG001
            port: object,  # noqa: ARG001
            *args: object,  # noqa: ARG001
            **kwargs: object,  # noqa: ARG001
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            return [(2, 1, 6, "", (ip, 0))]

        return _inner

    def test_hostname_resolving_to_private_rejected(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """主机名 DNS 解析到内网 IP → 拒绝（防 DNS 指向内网）。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            self._fake_getaddrinfo("10.1.2.3"),
        )
        _expect_rejected("https://internal.attacker.example/v1")

    def test_hostname_resolving_to_public_allowed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """主机名解析到公网 IP → 放行。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            self._fake_getaddrinfo("93.184.216.34"),
        )
        validate_outbound_api_base("https://relay.example.com/v1")

    def test_unresolvable_hostname_rejected(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """无法解析的主机名 → 拒绝（无法核验即不放行）。"""

        def _boom(*args: object, **kwargs: object) -> list[object]:
            msg = "Name or service not known"
            raise OSError(msg)

        monkeypatch.setattr(socket, "getaddrinfo", _boom)
        _expect_rejected("https://no-such-host.invalid/v1")


class TestAllowlist:
    """白名单逃生口：设了白名单则 host 必须命中（命中即放行，含内网中转站）。"""

    def test_allowlisted_host_passes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """白名单命中的公网中转站放行。"""
        monkeypatch.setenv(
            url_guard._ENV_API_BASE_ALLOWLIST,
            "api.relay.example.com, other.example.com",
        )
        validate_outbound_api_base("https://api.relay.example.com/v1")

    def test_allowlisted_internal_host_bypasses_ssrf(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """显式白名单的内网中转站名跳过私网拦截（逃生口语义）。"""
        monkeypatch.setenv(
            url_guard._ENV_API_BASE_ALLOWLIST, "my-lan-relay",
        )
        # 即便 host 名指向内网，显式信任即放行（不触发 DNS / SSRF 检查）
        validate_outbound_api_base("http://my-lan-relay:8000/v1")

    def test_non_allowlisted_host_rejected(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """设了白名单但 host 不在其中 → 拒绝（即便是公网地址）。"""
        monkeypatch.setenv(
            url_guard._ENV_API_BASE_ALLOWLIST, "api.relay.example.com",
        )
        _expect_rejected("https://evil.example.org/v1")
