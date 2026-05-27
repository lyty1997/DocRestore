# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式强制 OCR PaddleOCR basic 的单元测试（B4 H5）。"""

from __future__ import annotations

from docrestore.pipeline.config import OCRConfig
from docrestore.pipeline.pipeline import _ocr_config_for_code_mode


def test_request_only_code_enable_forces_basic() -> None:
    """仅传 code.enable（ocr=None）时用默认配置强制 basic。"""
    eff = _ocr_config_for_code_mode(None, OCRConfig(paddle_pipeline="vl"))
    assert eff is not None
    assert eff.paddle_pipeline == "basic"


def test_request_vl_ocr_forced_to_basic() -> None:
    """请求级 ocr=vl 时也强制 basic（直接 API 客户端不会因 vl 失败）。"""
    eff = _ocr_config_for_code_mode(
        OCRConfig(paddle_pipeline="vl"), OCRConfig(paddle_pipeline="vl"),
    )
    assert eff is not None
    assert eff.paddle_pipeline == "basic"


def test_already_basic_passes_through_unchanged() -> None:
    """已是 basic 时原样返回，不必复制。"""
    basic = OCRConfig(paddle_pipeline="basic")
    assert (
        _ocr_config_for_code_mode(basic, OCRConfig(paddle_pipeline="vl")) is basic
    )
