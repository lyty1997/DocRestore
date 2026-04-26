# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式 HTTP 端到端验收：API 入口 → files-index.json + files/ 真落盘。

之前 routes 算了 code_cfg 但没透传给 TaskManager，pipeline 永远走文档分支，
输出只有 document.md 而没有源文件。本测试模拟真实链路：
  POST /tasks (code.enable=true)
    → TaskManager.create_task
    → run_task → Pipeline.process_tree
    → _stream_pipeline 走 _code_pipeline 分支
    → render_code_files 写 files-index.json + files/

OCR 用 spike lines.jsonl 注入 text_lines（绕过 GPU/PaddleOCR）。
LLM 关闭（model=""）避免依赖网络/key；只验证规则引擎产物正确。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from docrestore.api import routes
from docrestore.api.routes import router, set_task_manager
from docrestore.api.upload import upload_router
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import TaskManager

from tests.support.code_ocr_engine import CodeFixtureOCREngine

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPIKE_IMAGES = _REPO_ROOT / "test_images" / "age8-spike"
_SPIKE_LINES = _REPO_ROOT / "output" / "age8-probe-basic"


@pytest.fixture
def code_image_dir(tmp_path: Path) -> Path:
    """复制 spike JPG 到 tmp_path（OCR 引擎 scan_images 要求真实文件）。"""
    if not _SPIKE_IMAGES.exists() or not _SPIKE_LINES.exists():
        pytest.skip("spike 数据未生成（需 test_images/age8-spike + age8-probe-basic）")
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    for jpg in sorted(_SPIKE_IMAGES.glob("DSC*.JPG")):
        if (_SPIKE_LINES / jpg.stem / "lines.jsonl").exists():
            shutil.copy(jpg, img_dir / jpg.name)
    return img_dir


@pytest.fixture
async def code_api_client(
    tmp_path: Path,
) -> AsyncIterator[AsyncClient]:
    """使用 CodeFixtureOCREngine + 关闭 LLM 的 API client。

    关掉 LLM (model="") 让 _code_pipeline 跳过 CodeLLMRefiner —— 这样测试不
    依赖外部 API key，只验证 routes/TaskManager/Pipeline/render_code_files
    的核心链路。LLM 路径有单独的 cloud/local 单元测试覆盖。
    """
    config = PipelineConfig(
        llm=LLMConfig(model=""),
        db_path=str(tmp_path / "test.db"),
    )
    pipeline = Pipeline(config)
    engine = CodeFixtureOCREngine(_SPIKE_LINES)
    pipeline.set_ocr_engine(engine)
    await pipeline.initialize()

    manager = TaskManager(pipeline)
    set_task_manager(manager)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(upload_router, prefix="/api/v1")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac

    await pipeline.shutdown()
    set_task_manager(None)


async def _wait_until_terminal(
    client: AsyncClient,
    task_id: str,
    timeout_s: float = 30.0,
) -> str:
    """轮询任务状态到 completed/failed，返回最终状态。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200, resp.text
        status: str = resp.json()["status"]
        if status in ("completed", "failed"):
            return status
        await asyncio.sleep(0.05)
    pytest.fail(f"task {task_id} 超时未结束（{timeout_s}s）")
    return "timeout"  # unreachable, silence mypy


class TestCodeModeE2E:
    """端到端：HTTP API 入口 → 文件系统产物。"""

    @pytest.mark.asyncio
    async def test_files_index_and_dir_emitted(
        self,
        code_api_client: AsyncClient,
        code_image_dir: Path,
        tmp_path: Path,
    ) -> None:
        """POST /tasks(code.enable=true) → files-index.json + files/ 真落盘。"""
        out_dir = tmp_path / "out"

        resp = await code_api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(code_image_dir),
                "output_dir": str(out_dir),
                "code": {"enable": True},
            },
        )
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]

        status = await _wait_until_terminal(code_api_client, task_id)
        mgr = routes._task_manager
        snapshot = mgr._tasks[task_id] if mgr is not None else None
        assert status == "completed", (
            f"task 未成功，状态={status}；task={snapshot}"
        )

        # 关键断言 1：files-index.json 真落盘（文档模式不会有这个文件）
        index_path = out_dir / "files-index.json"
        assert index_path.exists(), (
            f"代码模式产物 files-index.json 未生成；out_dir 内容: "
            f"{sorted(p.name for p in out_dir.iterdir())}"
        )

        # 关键断言 2：files/ 目录有源文件
        files_dir = out_dir / "files"
        assert files_dir.exists(), "files/ 目录未生成"
        emitted = [p for p in files_dir.rglob("*") if p.is_file()]
        assert len(emitted) >= 1, (
            f"files/ 下无源文件；index={json.loads(index_path.read_text())}"
        )

        # 关键断言 3：index 内容合法（与 AGE-52 验收一致）
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert isinstance(index, list)
        assert index, "files-index.json 为空"
        required = {"path", "filename", "language", "source_pages",
                    "line_count", "line_no_range", "flags"}
        for entry in index:
            assert required.issubset(entry)

        # 关键断言 4：chromium 路径出现（spike 数据特征）
        all_paths = " ".join(e["path"] for e in index)
        assert "media/gpu/openmax" in all_paths, (
            f"未恢复 chromium 路径，paths: {all_paths}"
        )

    @pytest.mark.asyncio
    async def test_code_mode_off_falls_back_to_doc(
        self,
        code_api_client: AsyncClient,
        code_image_dir: Path,
        tmp_path: Path,
    ) -> None:
        """code.enable=false / 不传 → 走文档分支，不应有 files-index.json。

        守护反向兼容：禁用代码模式时旧文档行为不破。
        """
        out_dir = tmp_path / "out_doc"

        resp = await code_api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(code_image_dir),
                "output_dir": str(out_dir),
                # 不传 code 字段
            },
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        # 文档模式会因为 spike OCR 无 raw_text 失败；这里只关心**没有**走代码分支
        # （即 files-index.json 不该出现）。所以不强求 completed。
        await _wait_until_terminal(
            code_api_client, task_id, timeout_s=10.0,
        )
        assert not (out_dir / "files-index.json").exists()
        assert not (out_dir / "files").exists()
