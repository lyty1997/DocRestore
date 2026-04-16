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
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
