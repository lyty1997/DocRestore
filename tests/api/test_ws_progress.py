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

"""WebSocket 进度推送测试（AGE-12）。

目标：
- A：验证建立连接后可以收到 TaskProgress 推送，且不止首帧
- B：验证同一任务多客户端订阅时，双方都能收到后续进度
- C：验证断开连接后 subscriber 资源能被清理（避免泄漏）

说明：Starlette/FastAPI 的 WebSocket 测试使用同步 TestClient，
因此本文件测试为同步用例。
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docrestore.api.routes import router, set_task_manager
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import TaskManager

from ..conftest import TEST_STEMS
from ..support.ocr_engine import FixtureOCREngine


@dataclass(frozen=True)
class WsTestEnv:
    """WS 测试环境。"""

    client: TestClient
    manager: TaskManager


@pytest.fixture
def work_dir(tmp_path: Path, require_ocr_data: Path) -> Path:
    """准备工作目录：input/ 放假图片，output/ 放 OCR 数据。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    stems = TEST_STEMS[:2]
    for stem in stems:
        (input_dir / f"{stem}.JPG").write_bytes(b"fake")
        src = require_ocr_data / f"{stem}_OCR"
        if src.exists():
            shutil.copytree(src, output_dir / f"{stem}_OCR")

    return tmp_path


@pytest.fixture
def env() -> Iterator[WsTestEnv]:
    """创建测试环境：手动初始化 Pipeline + TaskManager。"""
    config = PipelineConfig()
    pipeline = Pipeline(config)
    engine = FixtureOCREngine()
    pipeline.set_ocr_engine(engine)
    asyncio.run(pipeline.initialize())

    manager = TaskManager(pipeline)
    set_task_manager(manager)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    with TestClient(app) as tc:
        yield WsTestEnv(client=tc, manager=manager)

    asyncio.run(pipeline.shutdown())
    set_task_manager(None)


def _create_task(env: WsTestEnv, work_dir: Path) -> str:
    """创建任务并返回 task_id。"""
    resp = env.client.post(
        "/api/v1/tasks",
        json={
            "image_dir": str(work_dir / "input"),
            "output_dir": str(work_dir / "output"),
        },
    )
    assert resp.status_code == 200

    data = resp.json()
    task_id = data["task_id"]
    assert isinstance(task_id, str)
    return task_id


def _wait_for_subscriber_count(
    env: WsTestEnv,
    task_id: str,
    expected: int,
    timeout_s: float = 1.0,
) -> None:
    """等待 subscriber_count 达到预期值（用于断言资源清理）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if env.manager.subscriber_count(task_id) == expected:
            return
        time.sleep(0.01)

    assert env.manager.subscriber_count(task_id) == expected


@pytest.mark.usefixtures("require_ocr_data")
class TestTaskProgressWebSocket:
    """WebSocket 进度推送测试"""

    def test_ws_progress_receive_two_messages(
        self,
        env: WsTestEnv,
        work_dir: Path,
    ) -> None:
        """A：创建任务并连接 WS，收到至少两条进度消息。

        说明：服务端会在 task.progress 为空时先发一条“等待开始”的首帧快照。
        本用例要求后续还能收到真实进度（第 2 条消息）。
        """
        task_id = _create_task(env, work_dir)

        with env.client.websocket_connect(
            f"/api/v1/tasks/{task_id}/progress"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["stage"] in {
                "ocr",
                "clean",
                "merge",
                "refine",
                "render",
            }

            msg2 = ws.receive_json()
            assert msg2["stage"] in {
                "ocr",
                "clean",
                "merge",
                "refine",
                "render",
            }
            assert msg2.get("message") != "等待开始"

    def test_ws_progress_two_clients_receive_followup(
        self,
        env: WsTestEnv,
        work_dir: Path,
    ) -> None:
        """B：同一任务两个 WS 客户端都能收到后续进度。"""
        task_id = _create_task(env, work_dir)

        with env.client.websocket_connect(
            f"/api/v1/tasks/{task_id}/progress"
        ) as ws1, env.client.websocket_connect(
            f"/api/v1/tasks/{task_id}/progress"
        ) as ws2:
            _ = ws1.receive_json()
            _ = ws2.receive_json()

            msg2_1 = ws1.receive_json()
            msg2_2 = ws2.receive_json()

            assert msg2_1.get("message") != "等待开始"
            assert msg2_2.get("message") != "等待开始"

    def test_ws_disconnect_cleanup_subscriber(
        self,
        env: WsTestEnv,
        work_dir: Path,
    ) -> None:
        """C：断开连接后订阅者能被清理。"""
        task_id = _create_task(env, work_dir)

        with env.client.websocket_connect(
            f"/api/v1/tasks/{task_id}/progress"
        ) as ws:
            _ = ws.receive_json()
            _wait_for_subscriber_count(env, task_id, expected=1)

        _wait_for_subscriber_count(env, task_id, expected=0)
