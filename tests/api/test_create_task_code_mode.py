# Copyright 2026 @lyty1997

"""AGE-51 API 接受 code 字段 + 自动 override OCR pipeline 的单测"""

from __future__ import annotations

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
