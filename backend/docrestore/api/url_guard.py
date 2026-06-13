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

"""出站地址安全守卫：校验请求级可控的 LLM ``api_base``，防 SSRF + 数据外泄。

背景（#33）：产品保留"中转站"能力——前端可填自定义 ``api_base`` 把 LLM 文本
（可能含原文 / PII）发往用户指定的兼容端点。但请求级可控的出站地址若不设防，
等于把桌面服务变成 SSRF 跳板：指向 ``169.254.169.254``（云元数据）、``10.x`` /
``192.168.x``（内网横向）或攻击者公网地址（数据外泄）。

策略（两档）：

- **默认（无白名单）**：放行公网 http(s) 地址**与环回**（同机本地 LLM 的合法
  目标），但解析 host 的全部 IP，任一落入私网 / 链路本地 / 保留 / 多播 / 未指定
  即拒。挡住 SSRF 核心面（云元数据 / 内网横向），同时不误杀本地 LLM。
- **设了白名单**（env ``DOCRESTORE_LLM_API_BASE_ALLOWLIST``，逗号分隔 host）：
  host 必须命中白名单——命中即放行（含用户**显式信任**的内网中转站，跳过私网
  拦截，这是逃生口的意义）；未命中一律拒。

残留风险：DNS rebinding（校验时解析到公网、litellm 实际发包时 TTL 过期重绑内网）
未防——需 connect 级 IP pin，属过度工程，本阶段记入 known-issues 不实现。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from contextlib import suppress
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import urlsplit

from docrestore.api.errors import APIErrorCode, ApiBusinessError

logger = logging.getLogger(__name__)

#: 白名单环境变量名（逗号分隔的 host 列表；空 / 未设 = 不启用白名单）。
_ENV_API_BASE_ALLOWLIST = "DOCRESTORE_LLM_API_BASE_ALLOWLIST"

#: 允许的 URL scheme（出站只走 HTTP 家族）。
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _reject(reason: str) -> ApiBusinessError:
    """构造统一的拒绝异常（detail 中文供日志 / 旧客户端，code 供前端 i18n）。"""
    return ApiBusinessError(
        APIErrorCode.LLM_API_BASE_REJECTED,
        400,
        f"LLM api_base 被安全策略拒绝：{reason}",
    )


def _load_allowlist() -> frozenset[str]:
    """读取并规整白名单 host（小写、去空白、去空项）。"""
    raw = os.environ.get(_ENV_API_BASE_ALLOWLIST, "")
    return frozenset(
        host.strip().lower() for host in raw.split(",") if host.strip()
    )


def _ip_is_disallowed(ip: IPv4Address | IPv6Address) -> bool:
    """SSRF 防护：判定 IP 是否为**禁止**的出站目标。

    环回（127.0.0.0/8 / ``::1``）**放行**——本地 LLM（同机 ollama / vLLM）的
    合法目标，且单用户桌面下环回 SSRF 价值极低；高价值目标（云元数据
    169.254 / 链路本地、RFC1918 内网横向）仍拦。IPv4-mapped IPv6
    （``::ffff:a.b.c.d``）按内嵌 IPv4 判定，堵 ``::ffff:169.254.x.x`` 这类
    映射旁路（环回映射 ``::ffff:127.0.0.1`` 同样按环回放行）。
    """
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return False
    return (
        ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_ips(host: str) -> list[IPv4Address | IPv6Address]:
    """把 host 解析为 IP 列表：字面量 IP 直接解析，否则走 DNS（getaddrinfo）。

    解析失败 / 无可用结果 → 拒绝（无法核验即不放行）。**阻塞调用**，调用方需
    用 ``asyncio.to_thread`` 包裹避免卡事件循环。
    """
    with suppress(ValueError):
        return [ipaddress.ip_address(host)]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        logger.warning("api_base host 无法解析: %s (%s)", host, exc)
        raise _reject("无法解析的主机") from exc
    ips: list[IPv4Address | IPv6Address] = []
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]  # 去掉 IPv6 scope id
        with suppress(ValueError):
            ips.append(ipaddress.ip_address(addr))
    if not ips:
        raise _reject("无法解析的主机")
    return ips


def validate_outbound_api_base(api_base: str) -> None:
    """校验请求级 LLM ``api_base``；非法即抛 ``ApiBusinessError(400)``。

    空串 → 直接返回（用后端默认 api_base，不校验）。**含阻塞 DNS**，
    异步上下文请 ``await asyncio.to_thread(validate_outbound_api_base, value)``。
    """
    value = api_base.strip()
    if not value:
        return

    parts = urlsplit(value)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise _reject("仅允许 http/https 地址")
    host = parts.hostname
    if not host:
        raise _reject("缺少主机名")

    allowlist = _load_allowlist()
    if allowlist:
        # 设了白名单：命中即放行（含用户显式信任的内网中转站），否则拒。
        if host.lower() in allowlist:
            return
        logger.warning("api_base host 不在白名单内: %s", host)
        raise _reject("主机不在服务端白名单内")

    # 无白名单：默认放行公网，但解析全部 IP 挡 SSRF（私网 / 环回 / 链路本地等）。
    for ip in _resolve_ips(host):
        if _ip_is_disallowed(ip):
            logger.warning(
                "api_base 指向非公网地址，拒绝: host=%s ip=%s", host, ip,
            )
            raise _reject("禁止指向私网 / 链路本地 / 内网地址")
