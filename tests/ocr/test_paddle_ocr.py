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
        # drain task 会立刻 readline → 返回 b"" 表示 EOF，干净退出
        self.stderr.readline = AsyncMock(return_value=b"")
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


# ── seq 协议与 resync 回归 ──────────────────────────────

def _sent_payloads(mock_process: MockProcess) -> list[dict[str, object]]:
    """取所有已写入 stdin 的 JSON payload。"""
    return [
        json.loads(call.args[0].decode("utf-8"))
        for call in mock_process.stdin.write.call_args_list
    ]


def _make_readline(responses: list[bytes]) -> object:
    """把一串字节响应串成 stdout.readline 的 async mock。"""

    async def readline() -> bytes:
        if responses:
            return responses.pop(0)
        return b""

    return readline


@pytest.mark.asyncio
async def test_send_command_injects_monotonic_seq(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """每次 _send_command 请求 payload 必须带自增 seq。"""
    mock_process.set_responses([
        {"ok": True, "seq": 1},  # initialize
        {"ok": True, "seq": 2},  # shutdown
    ])
    original_exists = Path.exists

    def mock_exists(p: Path) -> bool:
        return True if str(p) == paddle_config.paddle_python else original_exists(p)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()
        await engine.shutdown()

    payloads = _sent_payloads(mock_process)
    seqs = [p["seq"] for p in payloads]
    assert seqs == [1, 2], f"seq 非严格单调递增: {seqs}"


@pytest.mark.asyncio
async def test_send_command_drops_stale_response(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """响应 seq 落后于期望时应被丢弃，继续读下一条直到 seq 对齐。"""
    responses = [
        json.dumps({"ok": True, "seq": 1}).encode() + b"\n",  # initialize
        # OCR 命令对应 seq=2；先混入一条旧残留 seq=1、再给出 seq=2 真响应
        json.dumps({"ok": True, "stale": True, "seq": 1}).encode() + b"\n",
        json.dumps({
            "ok": True,
            "seq": 2,
            "raw_text": "# t",
            "image_size": [10, 10],
            "image_count": 0,
            "ocr_dir": "",
        }).encode() + b"\n",
    ]
    mock_process.stdout.readline = _make_readline(responses)

    original_exists = Path.exists

    def mock_exists(p: Path) -> bool:
        return True if str(p) == paddle_config.paddle_python else original_exists(p)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()
        # 直接构造最小 ocr 命令，绕开 PaddleOCREngine.ocr 的文件系统副作用
        resp = await engine._send_command({"cmd": "ocr"})
        assert resp["seq"] == 2
        assert resp.get("stale") is not True


@pytest.mark.asyncio
async def test_cancel_records_pending_seq_and_resync_drains(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """_send_command 被 cancel 时记下 pending_seq；下次 _resync_if_needed
    读到匹配 seq 的残留响应即完成同步，不触发 _restart_worker。"""
    # initialize 正常；cancel 发生后 worker 仍会把请求响应写出
    init_resp = json.dumps({"ok": True, "seq": 1}).encode() + b"\n"
    # cancel 期间模拟 readline 抛 CancelledError
    # resync 时模拟 worker 先写出一条更早的残留再写出 pending seq=2
    resync_resps = [
        json.dumps({"ok": True, "stale": True, "seq": 0}).encode() + b"\n",
        json.dumps({"ok": True, "seq": 2}).encode() + b"\n",
    ]
    readline_plan = [init_resp, "CANCEL", *resync_resps]

    async def readline() -> bytes:
        if not readline_plan:
            return b""
        item = readline_plan.pop(0)
        if item == "CANCEL":
            raise asyncio.CancelledError
        assert isinstance(item, bytes)
        return item

    mock_process.stdout.readline = readline

    original_exists = Path.exists

    def mock_exists(p: Path) -> bool:
        return True if str(p) == paddle_config.paddle_python else original_exists(p)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()

        # 第一次 send 遇到 CancelledError → 记录 pending
        with pytest.raises(asyncio.CancelledError):
            await engine._send_command({"cmd": "ocr"})
        assert engine._pending_resync is True
        assert engine._pending_seq == 2

        # 预期：resync 丢弃 seq=0 残留、对齐 seq=2 完成
        with patch.object(
            engine, "_restart_worker", new=AsyncMock(),
        ) as restart_mock:
            await engine._resync_if_needed()
            restart_mock.assert_not_called()

        assert engine._pending_resync is False
        assert engine._pending_seq is None


@pytest.mark.asyncio
async def test_resync_timeout_falls_back_to_restart(
    paddle_config: OCRConfig,
    mock_process: MockProcess,
) -> None:
    """resync 读超时应回退 _restart_worker 兜底。"""
    mock_process.set_responses([{"ok": True, "seq": 1}])  # initialize

    original_exists = Path.exists

    def mock_exists(p: Path) -> bool:
        return True if str(p) == paddle_config.paddle_python else original_exists(p)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()
        # 手动置位 pending
        engine._pending_resync = True
        engine._pending_seq = 99

        async def hang_forever() -> bytes:
            await asyncio.sleep(10)
            return b""

        mock_process.stdout.readline = hang_forever

        with (
            patch.object(
                engine, "_restart_worker", new=AsyncMock(),
            ) as restart_mock,
            patch.object(engine, "_get_timeout", return_value=0),
        ):
            await engine._resync_if_needed()
            restart_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialize_skips_blank_and_log_lines(
    paddle_config: OCRConfig,
) -> None:
    """回归 2026-04-27：worker stdout 在 init 期间夹带空行 / 日志行时，
    不应抛 JSONDecodeError 把整个 task 推到 failed —— 应跳过非 JSON 行
    继续读到合法响应。
    """
    proc = MockProcess()

    # readline 返回序列：空行 → 日志噪声 → 合法 JSON
    lines: list[bytes] = [
        b"\n",                                        # 空行（用户报告的
                                                      # 直接触发条件）
        b"INFO 04-27 09:00 vllm.engine: warming up\n",  # vLLM 日志混入
        b"\n",                                        # 又一个空行
        b'{"ok": true}\n',                            # 合法响应
    ]
    idx = {"i": 0}

    async def staged_readline() -> bytes:
        i = idx["i"]
        if i >= len(lines):
            return b""
        idx["i"] = i + 1
        return lines[i]

    proc.stdout.readline = staged_readline

    original_exists = Path.exists

    def mock_exists(path_self: Path) -> bool:
        if str(path_self) == paddle_config.paddle_python:
            return True
        return original_exists(path_self)

    with (
        patch.object(Path, "exists", lambda p: mock_exists(p)),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=proc,
        ),
    ):
        engine = PaddleOCREngine(paddle_config)
        await engine.initialize()
        assert engine.is_ready
        # 4 行 stdout 全部消费完
        assert idx["i"] == len(lines)
