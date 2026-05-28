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

"""EngineManager 单元测试（纯 mock，CI 友好）

覆盖：
- ensure() 幂等：相同 model+gpu 连续调用只创建一次引擎
- ensure() 切换：model 或 gpu 变化时释放旧引擎 + 创建新引擎
- ensure() 初始化异常时清理半成品状态
- shutdown() 调用引擎 shutdown
- _extract_stderr_message 五个关键分支
- _start_ppocr_server：未配置 paddle_server_python 时跳过
- 进程级兜底清理（atexit / signal / 启动扫描）
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docrestore.ocr import engine_manager as em_mod
from docrestore.ocr.engine_manager import EngineManager
from docrestore.pipeline.config import OCRConfig


def _make_manager(
    model: str = "paddle-ocr/ppocr-v4",
    gpu_id: str = "0",
) -> tuple[EngineManager, OCRConfig]:
    """构造 EngineManager 和对应配置。"""
    config = OCRConfig(model=model, gpu_id=gpu_id)
    gpu_lock = asyncio.Lock()
    manager = EngineManager(config, gpu_lock)
    return manager, config


def _make_mock_engine() -> MagicMock:
    """构造 mock 引擎（initialize/shutdown 均为 AsyncMock）。"""
    engine = MagicMock()
    engine.initialize = AsyncMock(return_value=None)
    engine.shutdown = AsyncMock(return_value=None)
    return engine


class TestEnsureIdempotent:
    """ensure() 在同一 model+gpu 上的幂等性"""

    @pytest.mark.asyncio
    async def test_same_model_only_creates_once(self) -> None:
        """相同 model+gpu 连续两次 ensure，create_engine 只调用一次。"""
        manager, _ = _make_manager(model="deepseek/ocr-2")
        mock_engine = _make_mock_engine()

        # deepseek 路径不走 ppocr-server，简化测试
        with patch(
            "docrestore.ocr.engine_manager.create_engine",
            return_value=mock_engine,
        ) as mock_create:
            e1 = await manager.ensure()
            e2 = await manager.ensure()

        assert e1 is e2 is mock_engine
        assert mock_create.call_count == 1
        assert mock_engine.initialize.await_count == 1
        assert manager.current_model == "deepseek/ocr-2"


class TestEnsureSwitching:
    """ensure() 在 model/gpu 变化时的切换行为"""

    @pytest.mark.asyncio
    async def test_model_change_triggers_shutdown_and_recreate(
        self,
    ) -> None:
        """model 从 A 切到 B 时，旧引擎 shutdown + 创建新引擎。"""
        manager, _ = _make_manager(model="deepseek/ocr-2")
        engine_a = _make_mock_engine()
        engine_b = _make_mock_engine()

        with patch(
            "docrestore.ocr.engine_manager.create_engine",
            side_effect=[engine_a, engine_b],
        ) as mock_create:
            await manager.ensure()
            # 换模型：传入新 config 触发切换
            new_cfg = OCRConfig(model="deepseek/ocr-vl", gpu_id="0")
            await manager.ensure(new_cfg)

        assert mock_create.call_count == 2
        engine_a.shutdown.assert_awaited_once()
        assert manager.engine is engine_b
        assert manager.current_model == "deepseek/ocr-vl"

    @pytest.mark.asyncio
    async def test_gpu_change_triggers_switch(self) -> None:
        """gpu_id 变化时也触发切换（即便 model 相同）。"""
        manager, _ = _make_manager(model="deepseek/ocr-2", gpu_id="0")
        engine_a = _make_mock_engine()
        engine_b = _make_mock_engine()

        with patch(
            "docrestore.ocr.engine_manager.create_engine",
            side_effect=[engine_a, engine_b],
        ):
            await manager.ensure()
            await manager.ensure(
                OCRConfig(model="deepseek/ocr-2", gpu_id="1"),
            )

        engine_a.shutdown.assert_awaited_once()
        assert manager.engine is engine_b

    @pytest.mark.asyncio
    async def test_init_failure_cleans_up(self) -> None:
        """initialize 抛异常时应清理半成品状态。"""
        manager, _ = _make_manager(model="deepseek/ocr-2")
        bad_engine = _make_mock_engine()
        bad_engine.initialize = AsyncMock(
            side_effect=RuntimeError("init failed"),
        )

        with (
            patch(
                "docrestore.ocr.engine_manager.create_engine",
                return_value=bad_engine,
            ),
            pytest.raises(RuntimeError, match="init failed"),
        ):
            await manager.ensure()

        # 半成品被清理：shutdown 被调用，当前 model 为空
        bad_engine.shutdown.assert_awaited()
        assert manager.engine is None
        assert manager.current_model == ""


class TestShutdown:
    """shutdown() 释放资源"""

    @pytest.mark.asyncio
    async def test_shutdown_calls_engine_shutdown(self) -> None:
        """shutdown() 调用当前引擎的 shutdown。"""
        manager, _ = _make_manager(model="deepseek/ocr-2")
        engine = _make_mock_engine()

        with patch(
            "docrestore.ocr.engine_manager.create_engine",
            return_value=engine,
        ):
            await manager.ensure()
        await manager.shutdown()

        engine.shutdown.assert_awaited_once()
        assert manager.engine is None

    @pytest.mark.asyncio
    async def test_shutdown_without_engine_is_noop(self) -> None:
        """未启动引擎时 shutdown() 不报错。"""
        manager, _ = _make_manager()
        # 不调用 ensure，直接 shutdown
        await manager.shutdown()
        assert manager.engine is None

    @pytest.mark.asyncio
    async def test_shutdown_stops_ppocr_even_if_engine_shutdown_cancelled(
        self,
    ) -> None:
        """engine.shutdown 抛 CancelledError 时 _stop_ppocr_server 仍被调用。

        回归保护 orphan vLLM EngineCore 问题：
        修复前 _shutdown_current 的 _stop_ppocr_server 不在 finally，
        engine.shutdown 抛 CancelledError 会跳过 ppocr-server 清理。
        """
        manager, _ = _make_manager(model="deepseek/ocr-2")
        engine = _make_mock_engine()
        engine.shutdown = AsyncMock(side_effect=asyncio.CancelledError)

        with patch(
            "docrestore.ocr.engine_manager.create_engine",
            return_value=engine,
        ):
            await manager.ensure()

        stop_ppocr_mock = AsyncMock(return_value=None)
        with (
            patch.object(manager, "_stop_ppocr_server", stop_ppocr_mock),
            pytest.raises(asyncio.CancelledError),
        ):
            await manager.shutdown()

        # 关键断言：即便 engine.shutdown 抛 CancelledError，
        # ppocr-server 清理也必须被调用（否则孤儿进程）
        stop_ppocr_mock.assert_awaited_once()
        assert manager.engine is None
        assert manager.current_model == ""


class TestExtractStderrMessage:
    """_extract_stderr_message 静态方法五分支"""

    def test_loading_shards_percent(self) -> None:
        msg = EngineManager._extract_stderr_message(
            "Loading safetensors checkpoint shards:  45% ..."
        )
        assert msg is not None
        assert "45%" in msg
        assert "加载模型权重" in msg

    def test_using_cached(self) -> None:
        assert (
            EngineManager._extract_stderr_message(
                "Using cached model from ~/.cache/..."
            )
            == "模型文件已缓存，跳过下载"
        )
        assert (
            EngineManager._extract_stderr_message(
                "file already exist, skip"
            )
            == "模型文件已缓存，跳过下载"
        )

    def test_checking_connectivity(self) -> None:
        assert (
            EngineManager._extract_stderr_message(
                "Checking connectivity to HuggingFace"
            )
            == "检查模型源连通性..."
        )

    def test_engine_core_init(self) -> None:
        assert (
            EngineManager._extract_stderr_message(
                "INFO EngineCore_DP0 pid=12345 init"
            )
            == "vLLM 推理引擎初始化中..."
        )

    def test_no_model_hoster_available(self) -> None:
        assert (
            EngineManager._extract_stderr_message(
                "ERROR: No model hoster is available"
            )
            == "模型源不可用，请检查网络连接"
        )

    def test_unknown_line_returns_none(self) -> None:
        """未匹配模式的日志行返回 None。"""
        assert (
            EngineManager._extract_stderr_message(
                "some random uninteresting log line"
            )
            is None
        )


class TestStartPpocrServer:
    """_start_ppocr_server：无 python 路径时跳过（默认场景）"""

    @pytest.mark.asyncio
    async def test_skip_when_paddle_server_python_empty(self) -> None:
        """paddle_server_python 为空（默认）时不启动子进程。"""
        manager, config = _make_manager(model="paddle-ocr/ppocr-v4")
        # config.paddle_server_python == "" (默认)

        await manager._start_ppocr_server(config, on_progress=None)

        # 无子进程启动，内部属性为 None
        assert manager._ppocr_server_proc is None

    @pytest.mark.asyncio
    async def test_skip_when_python_path_not_exists(
        self,
    ) -> None:
        """配置了路径但文件不存在时跳过，不报错。"""
        manager, config = _make_manager(model="paddle-ocr/ppocr-v4")
        config.paddle_server_python = "/nonexistent/bin/python"

        await manager._start_ppocr_server(config, on_progress=None)

        assert manager._ppocr_server_proc is None


# ─────────────────────────────────────────────────────────────
# 进程级兜底清理测试（atexit / signal / 启动扫描）
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def clean_pgid_state() -> Iterator[None]:
    """测试前后清理 engine_manager 全局 pgid 状态，避免测试间污染。"""
    em_mod._atexit_callbacks.clear()
    em_mod._tracked_pgids.clear()
    yield
    em_mod._atexit_callbacks.clear()
    em_mod._tracked_pgids.clear()


class TestKillPgidSync:
    """_kill_pgid_sync 同步清理进程组。"""

    def test_sigterm_clears_group(self) -> None:
        """SIGTERM 后首次存活探测即 ProcessLookupError → 不升级 SIGKILL。"""
        import signal as _signal

        calls: list[tuple[int, int]] = []

        def fake_killpg(pgid: int, sig: int) -> None:
            calls.append((pgid, sig))
            if sig == 0:
                raise ProcessLookupError

        with (
            patch(
                "docrestore.ocr.engine_manager.os.killpg",
                side_effect=fake_killpg,
            ),
            patch("docrestore.ocr.engine_manager.time.sleep"),
        ):
            em_mod._kill_pgid_sync(9999)

        assert (9999, _signal.SIGTERM) in calls
        assert (9999, _signal.SIGKILL) not in calls

    def test_sigkill_fallback_when_grace_exceeded(self) -> None:
        """grace 超时且进程组仍活 → SIGKILL。"""
        import signal as _signal

        calls: list[tuple[int, int]] = []

        def fake_killpg(pgid: int, sig: int) -> None:
            calls.append((pgid, sig))  # 无异常：进程仍活

        mono = iter([0.0, 100.0])

        with (
            patch(
                "docrestore.ocr.engine_manager.os.killpg",
                side_effect=fake_killpg,
            ),
            patch("docrestore.ocr.engine_manager.time.sleep"),
            patch(
                "docrestore.ocr.engine_manager.time.monotonic",
                side_effect=lambda: next(mono),
            ),
        ):
            em_mod._kill_pgid_sync(9999, grace_seconds=3.0)

        assert (9999, _signal.SIGTERM) in calls
        assert (9999, _signal.SIGKILL) in calls

    def test_initial_sigterm_lookup_error_returns_early(self) -> None:
        """初始 SIGTERM 就 ProcessLookupError（进程已死）→ 直接返回。"""
        with patch(
            "docrestore.ocr.engine_manager.os.killpg",
            side_effect=ProcessLookupError,
        ) as mock_killpg:
            em_mod._kill_pgid_sync(9999)
        assert mock_killpg.call_count == 1


@pytest.mark.usefixtures("clean_pgid_state")
class TestPgidTracking:
    """_track_pgid / _untrack_pgid atexit + signal 注册。"""

    def test_track_registers_atexit_and_signal(self) -> None:
        """_track_pgid 注册 atexit 回调并安装 signal handler。"""
        with (
            patch(
                "docrestore.ocr.engine_manager.atexit.register",
            ) as mock_reg,
            patch(
                "docrestore.ocr.engine_manager._install_signal_handlers",
            ) as mock_sig,
        ):
            em_mod._track_pgid(12345)

        assert 12345 in em_mod._tracked_pgids
        assert 12345 in em_mod._atexit_callbacks
        mock_reg.assert_called_once()
        mock_sig.assert_called_once()

    def test_track_idempotent(self) -> None:
        """同一 pgid 重复 track 只注册一次 atexit。"""
        with (
            patch(
                "docrestore.ocr.engine_manager.atexit.register",
            ) as mock_reg,
            patch(
                "docrestore.ocr.engine_manager._install_signal_handlers",
            ),
        ):
            em_mod._track_pgid(12345)
            em_mod._track_pgid(12345)
        assert mock_reg.call_count == 1

    def test_untrack_removes_atexit(self) -> None:
        """_untrack_pgid 调用 atexit.unregister 并清空名单。"""
        with (
            patch("docrestore.ocr.engine_manager.atexit.register"),
            patch(
                "docrestore.ocr.engine_manager.atexit.unregister",
            ) as mock_unreg,
            patch(
                "docrestore.ocr.engine_manager._install_signal_handlers",
            ),
        ):
            em_mod._track_pgid(12345)
            em_mod._untrack_pgid(12345)

        assert 12345 not in em_mod._tracked_pgids
        assert 12345 not in em_mod._atexit_callbacks
        mock_unreg.assert_called_once()

    def test_untrack_unknown_is_noop(self) -> None:
        """untrack 从未 track 过的 pgid 不报错，也不触发 unregister。"""
        with patch(
            "docrestore.ocr.engine_manager.atexit.unregister",
        ) as mock_unreg:
            em_mod._untrack_pgid(99999)
        mock_unreg.assert_not_called()


@pytest.mark.usefixtures("clean_pgid_state")
class TestSighupHandler:
    """_sighup_handler：SIGHUP 时 killpg + 转发 SIGTERM 给自身。"""

    def test_sighup_kills_all_tracked_and_forwards_sigterm(self) -> None:
        import signal as _signal

        em_mod._tracked_pgids.add(111)
        em_mod._tracked_pgids.add(222)

        killpg_calls: list[tuple[int, int]] = []
        kill_calls: list[tuple[int, int]] = []

        def fake_killpg(pgid: int, sig: int) -> None:
            killpg_calls.append((pgid, sig))

        def fake_kill(pid: int, sig: int) -> None:
            kill_calls.append((pid, sig))

        with (
            patch(
                "docrestore.ocr.engine_manager.os.killpg",
                side_effect=fake_killpg,
            ),
            patch(
                "docrestore.ocr.engine_manager.os.kill",
                side_effect=fake_kill,
            ),
            patch(
                "docrestore.ocr.engine_manager.os.getpid",
                return_value=777,
            ),
        ):
            em_mod._sighup_handler(_signal.SIGHUP, None)

        assert (111, _signal.SIGTERM) in killpg_calls
        assert (222, _signal.SIGTERM) in killpg_calls
        assert (777, _signal.SIGTERM) in kill_calls


class TestExtractPaddleocrPgid:
    """_extract_paddleocr_pgid：/proc 解析。"""

    def test_valid_paddleocr_returns_pgid(self, tmp_path: Path) -> None:
        """cmdline 含 paddleocr + genai_server → 解析出 pgid。"""
        proc_dir = tmp_path / "proc" / "1234"
        proc_dir.mkdir(parents=True)
        (proc_dir / "cmdline").write_bytes(
            b"python\x00-m\x00paddleocr\x00genai_server\x00--port\x008119"
        )
        # /proc/<pid>/stat 格式：pid (comm) state ppid pgid sid ...
        (proc_dir / "stat").write_text(
            "1234 (python) S 1 5678 5678 0 0",
        )

        real_open = open  # 保存未被 patch 的 open

        # mock 需覆盖 open 全签名，统一用 Any 避免类型体操。
        def fake_open(  # noqa: ANN401
            path: Any, *args: Any, **kwargs: Any,
        ) -> Any:
            p = str(path)
            if p.startswith("/proc/1234/"):
                real = tmp_path / p.lstrip("/")
                return real_open(real, *args, **kwargs)
            msg = f"unexpected path: {p}"
            raise OSError(msg)

        with patch("builtins.open", side_effect=fake_open):
            assert em_mod._extract_paddleocr_pgid("1234") == 5678

    def test_non_paddleocr_returns_none(self, tmp_path: Path) -> None:
        """cmdline 不含 paddleocr/genai_server → 返回 None。"""
        proc_dir = tmp_path / "proc" / "1234"
        proc_dir.mkdir(parents=True)
        (proc_dir / "cmdline").write_bytes(
            b"python\x00app.py\x00--port\x008000",
        )
        real_open = open

        def fake_open(  # noqa: ANN401
            path: Any, *args: Any, **kwargs: Any,
        ) -> Any:
            p = str(path)
            if p.startswith("/proc/1234/"):
                real = tmp_path / p.lstrip("/")
                return real_open(real, *args, **kwargs)
            msg = f"unexpected: {p}"
            raise OSError(msg)

        with patch("builtins.open", side_effect=fake_open):
            assert em_mod._extract_paddleocr_pgid("1234") is None

    def test_cmdline_read_fails_returns_none(self) -> None:
        """OSError 读不到 cmdline → 返回 None。"""
        with patch("builtins.open", side_effect=OSError("no such file")):
            assert em_mod._extract_paddleocr_pgid("1234") is None


class TestCleanupStaleServers:
    """cleanup_stale_ppocr_servers 启动扫描。"""

    def test_cleans_stale_pgids_dedup(self) -> None:
        """两个 pid 同一 pgid → 只 kill 一次该 pgid。"""
        with (
            patch(
                "docrestore.ocr.engine_manager.sys.platform", "linux",
            ),
            patch(
                "docrestore.ocr.engine_manager.os.listdir",
                return_value=["1", "2", "abc", "3"],
            ),
            patch(
                "docrestore.ocr.engine_manager._extract_paddleocr_pgid",
                side_effect=lambda pid: {
                    "1": 5000, "2": 5000, "3": None,
                }.get(pid),
            ),
            patch(
                "docrestore.ocr.engine_manager._kill_pgid_sync",
            ) as mock_kill,
        ):
            cleaned = em_mod.cleanup_stale_ppocr_servers()

        assert cleaned == [5000]
        mock_kill.assert_called_once_with(5000)

    def test_non_linux_returns_empty(self) -> None:
        with patch(
            "docrestore.ocr.engine_manager.sys.platform", "darwin",
        ):
            assert em_mod.cleanup_stale_ppocr_servers() == []

    def test_listdir_oserror_returns_empty(self) -> None:
        with (
            patch(
                "docrestore.ocr.engine_manager.sys.platform", "linux",
            ),
            patch(
                "docrestore.ocr.engine_manager.os.listdir",
                side_effect=OSError,
            ),
        ):
            assert em_mod.cleanup_stale_ppocr_servers() == []
