# Copyright 2026 @lyty1997

"""AGE-51 API 接受 code 字段 + 自动 override OCR pipeline 的单测"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api import routes
from docrestore.api.schemas import (
    CodeRestoreConfigRequest,
    CreateTaskRequest,
    OCRConfigRequest,
)


class TestCreateTaskRequestSchema:
    def test_code_field_default_none(self) -> None:
        req = CreateTaskRequest(image_dir="/var/data/x")
        assert req.code is None

    def test_code_field_accepts_enable(self) -> None:
        req = CreateTaskRequest(
            image_dir="/var/data/x",
            code=CodeRestoreConfigRequest(enable=True),
        )
        assert req.code is not None
        assert req.code.enable is True

    def test_code_with_output_files_dir(self) -> None:
        req = CreateTaskRequest(
            image_dir="/var/data/x",
            code=CodeRestoreConfigRequest(enable=True, output_files_dir="src"),
        )
        assert req.code is not None
        assert req.code.output_files_dir == "src"

    def test_code_compatible_with_ocr_override(self) -> None:
        """同时给 code 和 ocr → 都接收，不冲突"""
        req = CreateTaskRequest(
            image_dir="/var/data/x",
            ocr=OCRConfigRequest(gpu_id="0"),
            code=CodeRestoreConfigRequest(enable=True),
        )
        assert req.ocr is not None
        assert req.ocr.gpu_id == "0"
        assert req.code is not None
        assert req.code.enable is True


class TestCodeOcrOverride:
    """验证 code.enable=True 时 PipelineConfig 自动 override paddle_pipeline"""

    def test_pipeline_config_override(self) -> None:
        from docrestore.pipeline.config import (
            CodeRestoreConfig,
            PipelineConfig,
        )
        cfg = PipelineConfig(
            code=CodeRestoreConfig(enable=True),
        )
        # PipelineConfig.model_validator 自动 override
        assert cfg.ocr.paddle_pipeline == "basic"

    def test_pipeline_config_no_override_when_disabled(self) -> None:
        from docrestore.pipeline.config import (
            CodeRestoreConfig,
            PipelineConfig,
        )
        cfg = PipelineConfig(
            code=CodeRestoreConfig(enable=False),
        )
        # 默认 vl，不被改
        assert cfg.ocr.paddle_pipeline == "vl"

    def test_pipeline_config_user_explicit_basic_preserved(self) -> None:
        """用户显式指定 basic + code.enable=False → 仍是 basic（用户胜出）"""
        from docrestore.pipeline.config import (
            CodeRestoreConfig,
            OCRConfig,
            PipelineConfig,
        )
        cfg = PipelineConfig(
            ocr=OCRConfig(paddle_pipeline="basic"),
            code=CodeRestoreConfig(enable=False),
        )
        assert cfg.ocr.paddle_pipeline == "basic"


class TestCreateTaskCodeFlowsToManager:
    """端到端链路守护：POST /tasks 把 code 配置一路传到 TaskManager._tasks。

    曾经 routes.create_task 计算了 code_cfg 但没传给 manager.create_task，
    导致前端代码模式 checkbox 形同虚设；这条用例锁住链路不再悄悄断掉。
    """

    @pytest.mark.asyncio
    async def test_code_enable_persists_to_task(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        img_dir = tmp_path / "imgs"
        img_dir.mkdir()
        # 占位图避免 image_dir 校验失败；create_task 后立刻读 _tasks，
        # 不等 pipeline 真正跑完
        (img_dir / "DSC0001.jpg").write_bytes(b"\xff\xd8\xff\xe0")

        resp = await api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(img_dir),
                "output_dir": str(tmp_path / "out"),
                "code": {"enable": True, "output_files_dir": "src"},
            },
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        assert routes._task_manager is not None
        task = routes._task_manager._tasks[task_id]
        assert task.code is not None, "routes 没有把 code 透传给 TaskManager"
        assert task.code.enable is True
        assert task.code.output_files_dir == "src"
        # OCR pipeline 也应同步切到 basic
        assert task.ocr is not None
        assert task.ocr.paddle_pipeline == "basic"

    @pytest.mark.asyncio
    async def test_code_omitted_keeps_task_code_none(
        self, api_client: AsyncClient, tmp_path: Path,
    ) -> None:
        """请求未带 code → task.code is None（保留默认行为）"""
        img_dir = tmp_path / "imgs"
        img_dir.mkdir()
        (img_dir / "DSC0001.jpg").write_bytes(b"\xff\xd8\xff\xe0")

        resp = await api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(img_dir),
                "output_dir": str(tmp_path / "out"),
            },
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        assert routes._task_manager is not None
        task = routes._task_manager._tasks[task_id]
        assert task.code is None
