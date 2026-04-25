# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""AGE-55 + AGE-51 P0 配置单测：CodeRestoreConfig + paddle_pipeline 切换"""

from __future__ import annotations

from docrestore.pipeline.config import (
    CodeRestoreConfig,
    OCRConfig,
    PipelineConfig,
)


class TestCodeRestoreConfig:
    def test_defaults(self) -> None:
        cfg = CodeRestoreConfig()
        assert cfg.enable is False
        assert cfg.output_files_dir == "files"
        assert cfg.file_grouping_strategy == "tab_breadcrumb"

    def test_can_enable(self) -> None:
        cfg = CodeRestoreConfig(enable=True)
        assert cfg.enable is True


class TestPaddlePipelineSwitch:
    def test_default_vl(self) -> None:
        """默认 paddle_pipeline=vl（文档场景）"""
        cfg = OCRConfig()
        assert cfg.paddle_pipeline == "vl"

    def test_can_set_basic(self) -> None:
        cfg = OCRConfig(paddle_pipeline="basic")
        assert cfg.paddle_pipeline == "basic"


class TestCodeModeAutoOverride:
    """code.enable=True 自动 override paddle_pipeline → basic"""

    def test_code_enabled_overrides_default_vl(self) -> None:
        cfg = PipelineConfig(code=CodeRestoreConfig(enable=True))
        assert cfg.code.enable is True
        assert cfg.ocr.paddle_pipeline == "basic"

    def test_code_disabled_keeps_vl(self) -> None:
        cfg = PipelineConfig()
        assert cfg.code.enable is False
        assert cfg.ocr.paddle_pipeline == "vl"

    def test_explicit_user_pipeline_preserved_when_code_off(self) -> None:
        """code.enable=False + 用户显式 basic → 保留 basic（不被改成默认 vl）"""
        cfg = PipelineConfig(ocr=OCRConfig(paddle_pipeline="basic"))
        assert cfg.ocr.paddle_pipeline == "basic"

    def test_code_on_with_explicit_basic_unchanged(self) -> None:
        """code 模式 + 用户显式 basic → 仍是 basic"""
        cfg = PipelineConfig(
            code=CodeRestoreConfig(enable=True),
            ocr=OCRConfig(paddle_pipeline="basic"),
        )
        assert cfg.ocr.paddle_pipeline == "basic"
