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

"""访问日志脱敏：请求行 query string 里的 token 值打码，避免明文 token 落日志。

本服务用 ``?token=<token>`` 走 ``<img>`` / ``<a href>`` / WebSocket 鉴权（浏览器这些
场景无法设置 ``Authorization`` Header，见 :func:`docrestore.api.auth.require_auth`），
而 uvicorn 会把完整请求行（含 query string）写进日志，于是明文 token 被泄露，形如::

    # HTTP（uvicorn.access）
    127.0.0.1 - "GET /tasks/<id>/source-images/x.JPG?token=<明文> HTTP/1.1" 200
    # WebSocket 握手（uvicorn.error）
    127.0.0.1 - "WebSocket /tasks/<id>/progress?token=<明文>" [accepted]

token 即配对密钥（手机/前端凭它访问本机服务），落日志等同泄露。这里在 ``uvicorn.access``
与 ``uvicorn.error`` 两个 logger 上各挂 :class:`logging.Filter`，在 record 进 formatter
前就地把 token 值换成占位符；token 鉴权链路（query param 仍照常解析）完全不受影响。

已知限制：只识别 **字面** key 拼写（``token`` / ``api_token`` / ``access_token``）。
百分号编码的 key（如 ``%74oken=``）虽能被 ``parse_qsl`` 解码后过鉴权，但不被此处脱敏；
正常前端恒发字面 ``?token=``，且构造此类请求只泄露攻击者自己已持有的 token，风险低。
"""

from __future__ import annotations

import logging
import re

#: query string 里需要打码的参数名（大小写不敏感）。覆盖本服务的 ``token`` 以及
#: 常见同义命名，避免日后新增同类参数再漏一次。
_REDACT_QS_KEYS = ("token", "api_token", "access_token")

#: 匹配 ``?token=<value>`` / ``&token=<value>``；value 取到下一个 ``&`` / 空白 / 引号
#: 为止（token_urlsafe 值由 ``[A-Za-z0-9_-]`` 组成不含这些分隔符，故能精确截断）。
#: 前置 ``[?&]`` 锚点确保只动 query 参数，不会误伤自由文本里出现的 "token=" 子串。
_TOKEN_QS_RE = re.compile(
    r"([?&](?:" + "|".join(_REDACT_QS_KEYS) + r")=)[^&\s\"']+",
    re.IGNORECASE,
)

#: 打码占位符（保留参数名与 ``=``，只抹掉值，便于排错时仍看得出"带了 token"）。
_REDACTED = "<redacted>"


def redact_token_in_text(text: str) -> str:
    """把字符串里 query string 形态的 token 值替换成占位符，其余字符原样保留。"""
    return _TOKEN_QS_RE.sub(r"\1" + _REDACTED, text)


class AccessLogTokenRedactor(logging.Filter):
    """uvicorn 日志过滤器：把请求行 query string 里的 token 值打码。

    覆盖两类记录（都把 URL 作为 ``%s`` 位置参数塞进 ``record.args``）：

    - HTTP 访问日志（``uvicorn.access``）：``args=(client, method, path_with_query,
      http_version, status)``，URL 在第 3 项；
    - WebSocket 握手日志（``uvicorn.error``）：``args=(client, path_with_query)``，
      URL 在第 2 项（``'%s - "WebSocket %s" [accepted]'`` / ``... 403`` / ``... %d``）。

    为不依赖具体下标（也防 uvicorn 日后改 record 结构后静默失效），**遍历 args 里所有
    字符串项**逐一脱敏；在 formatter 读取前就地改写，兼容默认/自定义 formatter。
    若 record 已被预格式化进 ``msg``（args 为空），退回扫 ``msg`` 兜底。永不丢弃记录。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """就地脱敏 record，恒返回 True（只改写内容，不过滤掉任何日志）。"""
        args = record.args
        redacted_any = False
        if isinstance(args, tuple) and args:
            new_args: list[object] | None = None
            for i, item in enumerate(args):
                # 仅对含 "token=" 的字符串项脱敏（跳过 client_addr/method 等）。
                # 直接 .lower() 后判子串：大小写不敏感，覆盖非常规大小写的 query。
                if isinstance(item, str) and "token=" in item.lower():
                    redacted = redact_token_in_text(item)
                    if redacted != item:
                        if new_args is None:
                            new_args = list(args)
                        new_args[i] = redacted
            if new_args is not None:
                record.args = tuple(new_args)
                redacted_any = True
        if (
            not redacted_any
            and isinstance(record.msg, str)
            and "token=" in record.msg.lower()
        ):
            # 兜底：args 里没有可脱敏项（为空/None/无 token 串）时，扫已成品的 msg
            record.msg = redact_token_in_text(record.msg)
        return True


def install_access_log_redaction() -> None:
    """在 ``uvicorn.access`` 与 ``uvicorn.error`` logger 上幂等安装 token 打码过滤器。

    由 :func:`docrestore.api.app.create_app` 在启动时调用。``uvicorn.access`` 承载 HTTP
    访问日志、``uvicorn.error`` 承载 WebSocket 握手日志（两者都会打印含 ``?token=`` 的
    请求行）。uvicorn 已在加载 app 前 ``configure_logging`` 配好这两个 logger，这里只
    补挂 Filter；``create_app`` 可能被多次调用（测试 / 多 worker），故按类型去重保幂等。
    """
    for logger_name in ("uvicorn.access", "uvicorn.error"):
        target = logging.getLogger(logger_name)
        if not any(isinstance(f, AccessLogTokenRedactor) for f in target.filters):
            target.addFilter(AccessLogTokenRedactor())
