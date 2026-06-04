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

"""OCR worker 传输健壮性单测（#18 stderr 单读者 / #19 批量不丢页）。

不启动真实 worker 子进程：
- #18：直接测基类 stderr drain 的逐行 hook（替代另起协程并发 readline）。
- #19：用 __new__ + monkeypatch _send_command 构造短响应，验证拒绝静默丢页。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from docrestore.models import PageOCR
from docrestore.ocr.base import _drain_stream_to_logger
from docrestore.ocr.deepseek_ocr2 import DeepSeekOCR2Engine
from docrestore.pipeline.config import OCRConfig


class _FakeReader:
    """最小 StreamReader：按列表逐行返回，耗尽返回 b'' 表示 EOF。"""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


@pytest.mark.asyncio
async def test_drain_routes_each_line_to_on_line() -> None:
    """#18：drain 作为唯一读者，把每行经 on_line 转出（替代第二个并发 readline）。"""
    got: list[str] = []
    reader = _FakeReader([b"vLLM loading 30%\n", b"loading 80%\n"])

    await _drain_stream_to_logger(
        cast("asyncio.StreamReader", reader),
        "[test stderr]",
        on_line=got.append,
    )

    # 每行都被转给 on_line（init 进度即靠此推送，不再并发 readline 同一 reader）
    assert got == ["vLLM loading 30%", "loading 80%"]


def _dummy_page(path: Path) -> PageOCR:
    return PageOCR(
        image_path=path, image_size=(1, 1),
        raw_text="", cleaned_text="", output_dir=Path("/out"),
    )


@pytest.mark.asyncio
async def test_batch_raises_on_missing_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#19：worker batch 响应项数 < 请求页数 → 抛错，不静默丢页。"""
    eng = DeepSeekOCR2Engine.__new__(DeepSeekOCR2Engine)
    eng._config = OCRConfig()

    # 2 张图的 chunk，但 worker 只返回 1 项 → 旧实现静默丢第 2 页
    monkeypatch.setattr(
        eng, "_send_command",
        AsyncMock(return_value={
            "ok": True,
            "results": [{"ok": True, "raw_text": "x"}],
        }),
    )
    monkeypatch.setattr(
        eng, "_parse_single_result",
        lambda _item, path, _fallback: _dummy_page(path),
    )

    chunk = [Path("a.jpg"), Path("b.jpg")]
    with pytest.raises(RuntimeError, match="页数不一致"):
        await eng._send_ocr_batch_all(chunk, Path("/out"))
