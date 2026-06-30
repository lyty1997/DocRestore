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

"""访问日志 token 脱敏单元测试。

验证 ``?token=`` query param 的明文 token 不会出现在 uvicorn 访问日志里，
而非 token 的请求行原样保留、过滤器永不丢弃记录。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from uvicorn.logging import AccessFormatter

from docrestore.api.log_redaction import (
    AccessLogTokenRedactor,
    install_access_log_redaction,
    redact_token_in_text,
)

# 测试用的"明文 token"——非真实密钥，仅用来断言它不出现在脱敏输出里
_SECRET = "tok_S3cr3t_value_xyz-AB_cd"  # noqa: S105 — 测试用假 token
_PLACEHOLDER = "<redacted>"


def _make_access_record(path_with_query: str) -> logging.LogRecord:
    """构造 uvicorn.access 风格 LogRecord（args 为 5 元组，第 3 项是请求路径）。"""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:44906", "GET", path_with_query, "1.1", 200),
        exc_info=None,
    )


class TestRedactTokenInText:
    """``redact_token_in_text`` 纯函数行为。"""

    def test_redacts_token_query_param(self) -> None:
        """``?token=<明文>`` 的值被替换成占位符（保留参数名）。"""
        out = redact_token_in_text(f"/x?token={_SECRET}")
        assert out == f"/x?token={_PLACEHOLDER}"
        assert _SECRET not in out

    def test_redacts_token_when_not_first_param(self) -> None:
        """``&token=`` 同样脱敏，前面的非敏感参数原样保留。"""
        out = redact_token_in_text(f"/x?a=1&token={_SECRET}")
        assert out == f"/x?a=1&token={_PLACEHOLDER}"

    def test_stops_at_next_param_boundary(self) -> None:
        """token 值后接 ``&other=`` 时只抹 token 值，后续参数不受影响。"""
        out = redact_token_in_text(f"/x?token={_SECRET}&b=2")
        assert out == f"/x?token={_PLACEHOLDER}&b=2"
        assert _SECRET not in out

    def test_redacts_aliases_case_insensitive(self) -> None:
        """api_token / access_token / 大小写变体都脱敏。"""
        for key in ("api_token", "access_token", "Token", "TOKEN"):
            out = redact_token_in_text(f"/x?{key}={_SECRET}")
            assert _SECRET not in out
            assert _PLACEHOLDER in out

    def test_leaves_non_token_params_untouched(self) -> None:
        """无 token 参数的 URL 原样返回。"""
        original = "/x?name=IMG_0001.jpg&doc_dir=subA"
        assert redact_token_in_text(original) == original


class TestAccessLogTokenRedactor:
    """过滤器就地改写 LogRecord：脱敏后再交给 formatter。"""

    def test_redacts_token_in_request_line(self) -> None:
        """含 token 的请求行：脱敏后渲染消息里查不到明文 token。"""
        path = f"/api/v1/tasks/t1/source-images/x.JPG?token={_SECRET}"
        record = _make_access_record(path)
        assert AccessLogTokenRedactor().filter(record) is True
        rendered = record.getMessage()
        assert _SECRET not in rendered
        assert _PLACEHOLDER in rendered

    def test_non_token_request_line_unchanged(self) -> None:
        """不含 token 的请求行原样保留（仅证明无副作用）。"""
        record = _make_access_record("/api/v1/healthz")
        AccessLogTokenRedactor().filter(record)
        expected = '127.0.0.1:44906 - "GET /api/v1/healthz HTTP/1.1" 200'
        assert record.getMessage() == expected

    def test_filter_never_drops_record(self) -> None:
        """过滤器恒返回 True（只改写内容，绝不吞掉日志）。"""
        record = _make_access_record("/x")
        assert AccessLogTokenRedactor().filter(record) is True

    def test_fallback_scans_preformatted_msg(self) -> None:
        """args 为空、token 已落在 msg 里时退回扫 msg 脱敏。"""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=f'GET /x?token={_SECRET} HTTP/1.1',
            args=None,
            exc_info=None,
        )
        AccessLogTokenRedactor().filter(record)
        assert _SECRET not in record.getMessage()
        assert _PLACEHOLDER in record.getMessage()

    def test_redacts_websocket_handshake_record(self) -> None:
        """WebSocket 握手日志（uvicorn.error）URL 在 args[1]，也须脱敏。

        回归保护：WS 握手走 uvicorn.error logger，记录形如
        ``'%s - "WebSocket %s" [accepted]'``、args=(client, path_with_query)，
        URL 在第 2 项而非第 3 项。过滤器遍历所有 str 项才能覆盖。
        """
        path = f"/api/v1/tasks/t1/progress?token={_SECRET}"
        record = logging.LogRecord(
            name="uvicorn.error",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg='%s - "WebSocket %s" [accepted]',
            args=("127.0.0.1:50001", path),
            exc_info=None,
        )
        assert AccessLogTokenRedactor().filter(record) is True
        rendered = record.getMessage()
        assert _SECRET not in rendered
        assert _PLACEHOLDER in rendered


class TestInstallAccessLogRedaction:
    """在 uvicorn.access / uvicorn.error logger 上幂等安装过滤器。"""

    _NAMES = ("uvicorn.access", "uvicorn.error")

    @pytest.fixture(autouse=True)
    def _restore_filters(self) -> Iterator[None]:
        """快照并还原两个 logger 的 filters，避免污染其它测试。"""
        saved = {n: list(logging.getLogger(n).filters) for n in self._NAMES}
        yield
        for name, flt in saved.items():
            logging.getLogger(name).filters = flt

    def test_installs_once_on_both_loggers_idempotent(self) -> None:
        """两个 logger 各只挂一个 AccessLogTokenRedactor（反复 create_app 不重挂）。"""
        for name in self._NAMES:
            lg = logging.getLogger(name)
            lg.filters = [
                f for f in lg.filters if not isinstance(f, AccessLogTokenRedactor)
            ]
        install_access_log_redaction()
        install_access_log_redaction()
        for name in self._NAMES:
            installed = [
                f for f in logging.getLogger(name).filters
                if isinstance(f, AccessLogTokenRedactor)
            ]
            assert len(installed) == 1


class TestUvicornFormatterIntegration:
    """端到端：经 uvicorn 真实 AccessFormatter 渲染后，明文 token 不出现。

    锁定 uvicorn access record 契约（args[2]=请求路径）。若 uvicorn 改 record 结构
    导致脱敏失效，本用例会变红，而非只靠我们自造的 record 形状自证。
    """

    def test_secret_absent_after_real_formatter(self) -> None:
        """脱敏后的 record 经 AccessFormatter 渲染：无明文 token、有占位符。"""
        path = f"/api/v1/tasks/t1/source-images/x.JPG?token={_SECRET}"
        record = _make_access_record(path)
        AccessLogTokenRedactor().filter(record)
        formatted = AccessFormatter(use_colors=False).format(record)
        assert _SECRET not in formatted
        assert _PLACEHOLDER in formatted
