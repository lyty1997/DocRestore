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

"""PaddleOCREngine 单元测试（mock subprocess）"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docrestore.ocr.paddle_ocr import PaddleOCREngine
from docrestore.pipeline.config import OCRConfig


@pytest.fixture
def paddle_config() -> OCRConfig:
    """PaddleOCR 配置。"""
    return OCRConfig(
        model="paddle-ocr/ppocr-v4",
        paddle_python="/fake/conda/bin/python",
        paddle_ocr_timeout=60,
    )


class MockProcess:
    """模拟 asyncio.subprocess.Process。"""

    def __init__(self) -> None:
        # stdin: write/close 是同步方法，drain/wait_closed 是异步方法
        self.stdin = MagicMock()
        self.stdin.drain = AsyncMock()
        self.stdin.wait_closed = AsyncMock()
        self.stdout = AsyncMock()
        self.stderr = AsyncMock()
        self.returncode: int | None = None
        self._responses: list[dict[str, object]] = []
        self._terminated = False
        self._killed = False

    def set_responses(
        self, responses: list[dict[str, object]]
    ) -> None:
        """设置预定义的响应队列。"""
        self._responses = responses

    async def wait(self) -> int:
        """等待进程结束。"""
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        """终止进程。"""
        self.returncode = -15
        self._terminated = True

    def kill(self) -> None:
        """强制杀死进程。"""
        self.returncode = -9
        self._killed = True


@pytest.fixture
def mock_process() -> MockProcess:
    """创建 mock 进程。"""
    proc = MockProcess()

    # 模拟 stdout.readline() 返回 JSON 响应
    async def readline() -> bytes:
        if proc._responses:
            resp = proc._responses.pop(0)
            line = json.dumps(resp) + "\n"
            return line.encode("utf-8")
        return b""

    proc.stdout.readline = readline
    return proc


@pytest.mark.asyncio
async def test_initialize_success(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """测试初始化成功。"""
    mock_process.set_responses([{"ok": True}])

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()

        assert engine.is_ready
        # 验证发送了 initialize 命令
        mock_process.stdin.write.assert_called_once()
        call_args = mock_process.stdin.write.call_args[0][0]
        cmd = json.loads(call_args.decode("utf-8"))
        assert cmd["cmd"] == "initialize"


@pytest.mark.asyncio
async def test_initialize_worker_error(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """测试 worker 初始化失败。"""
    mock_process.set_responses([
        {"ok": False, "error": "模型加载失败"}
    ])

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        with pytest.raises(RuntimeError, match="模型加载失败"):
            await engine.initialize()


@pytest.mark.asyncio
async def test_ocr_success(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
    tmp_path: Path,
) -> None:
    """测试 OCR 成功。"""
    mock_process.set_responses([
        {"ok": True},  # initialize
        {  # ocr
            "ok": True,
            "raw_text": "# 标题\n\n![](images/0.jpg)",
            "image_size": [1920, 1080],
            "image_count": 1,
            "ocr_dir": str(tmp_path / "test_OCR"),
        },
    ])

    image_path = tmp_path / "test.jpg"
    image_path.write_bytes(b"fake")

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        # 只让 conda python 路径返回 True，其他走真实判断
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()

        result = await engine.ocr(image_path, tmp_path)

        assert result.image_path == image_path
        assert result.image_size == (1920, 1080)
        assert "标题" in result.raw_text
        assert len(result.regions) == 1
        assert result.regions[0].label == "image"


@pytest.mark.asyncio
async def test_shutdown(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """测试关闭引擎。"""
    mock_process.set_responses([
        {"ok": True},  # initialize
        {"ok": True},  # shutdown
    ])

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()
        await engine.shutdown()

        assert not engine.is_ready
        assert mock_process._terminated


@pytest.mark.asyncio
async def test_shutdown_fast_when_worker_unresponsive(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """worker 假死（readline 永不返回）时，shutdown 仍能快速完成。

    回归保护：修复前 _send_command 会等满 paddle_ocr_timeout=60s；
    修复后由 SHUTDOWN_COMMAND_TIMEOUT_SECONDS=3.0s 控制，总时长 < 5s。
    """
    import time

    mock_process.set_responses([{"ok": True}])  # only initialize

    # shutdown 期间 readline 永远 hang
    call_count = {"n": 0}

    async def readline() -> bytes:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 第一次是 initialize 的响应
            return (json.dumps({"ok": True}) + "\n").encode("utf-8")
        # 后续（shutdown 命令的响应）永远挂起
        await asyncio.Future()
        return b""  # 不会到达

    mock_process.stdout.readline = readline

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()

        start = time.monotonic()
        await engine.shutdown()
        elapsed = time.monotonic() - start

        # SHUTDOWN_COMMAND_TIMEOUT_SECONDS=3.0s + terminate 缓冲，总 < 5s
        assert elapsed < 5.0, (
            f"shutdown 耗时 {elapsed:.1f}s，远超 3s 超时"
        )
        assert mock_process._terminated
        assert not engine.is_ready


@pytest.mark.asyncio
async def test_shutdown_force_skips_graceful_command(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """shutdown(force=True) 跳过 graceful 命令，直接 terminate 进程。

    _restart_worker 场景：worker 已假死，发 shutdown 命令无意义。
    """
    import time

    mock_process.set_responses([{"ok": True}])  # only initialize

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()
        # 记录 initialize 后的 write 调用次数基线
        init_write_count = mock_process.stdin.write.call_count

        start = time.monotonic()
        await engine.shutdown(force=True)
        elapsed = time.monotonic() - start

        # force=True 应立即 terminate，不走 3s 超时
        assert elapsed < 1.0, f"force shutdown 耗时 {elapsed:.1f}s，应瞬时"
        # 没有新的 write 调用（没发 shutdown 命令）
        assert mock_process.stdin.write.call_count == init_write_count
        assert mock_process._terminated


@pytest.mark.asyncio
async def test_restart_worker_uses_force_shutdown(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """_restart_worker 内部调用 shutdown(force=True)，不发 graceful 命令。

    保证 OCR 超时后重启路径不会在 shutdown 命令上二次阻塞。
    """
    # initialize 两次响应（原始 + 重启后）
    mock_process.set_responses([{"ok": True}, {"ok": True}])

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()

        # 记录 initialize 后所有 write 调用的 cmd 字段
        sent_cmds_before = [
            json.loads(call.args[0].decode("utf-8"))["cmd"]
            for call in mock_process.stdin.write.call_args_list
        ]

        await engine._restart_worker()

        sent_cmds_after = [
            json.loads(call.args[0].decode("utf-8"))["cmd"]
            for call in mock_process.stdin.write.call_args_list
        ]
        # restart 期间不应发送 "shutdown" 命令
        new_cmds = sent_cmds_after[len(sent_cmds_before):]
        assert "shutdown" not in new_cmds, (
            f"_restart_worker 仍发送 shutdown 命令: {new_cmds}"
        )
        # initialize 命令被重新发送
        assert "initialize" in new_cmds
