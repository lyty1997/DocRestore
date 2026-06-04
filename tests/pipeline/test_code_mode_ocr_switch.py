# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式强制 PaddleOCR basic / PPT 模式强制 vl 的单元测试（B4 H5）。

两者共用 ``_ocr_config_force_pipeline``，分别锁定 basic / vl 的强制与穿透行为。
"""

from __future__ import annotations

from docrestore.pipeline.config import OCRConfig
from docrestore.pipeline.pipeline import (
    _ocr_config_for_code_mode,
    _ocr_config_for_ppt_mode,
)


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


def test_request_only_ppt_enable_forces_vl() -> None:
    """仅传 ppt（ocr=None）时用默认配置强制 vl（PPT 还原所需 markdown/公式/裁图）。"""
    eff = _ocr_config_for_ppt_mode(None, OCRConfig(paddle_pipeline="basic"))
    assert eff is not None
    assert eff.paddle_pipeline == "vl"


def test_request_basic_ocr_forced_to_vl() -> None:
    """请求级 ocr=basic 时也强制 vl，防止 PPT 静默降级为纯文字拼接。"""
    eff = _ocr_config_for_ppt_mode(
        OCRConfig(paddle_pipeline="basic"), OCRConfig(paddle_pipeline="basic"),
    )
    assert eff is not None
    assert eff.paddle_pipeline == "vl"


def test_ppt_already_vl_passes_through_unchanged() -> None:
    """已是 vl 时原样返回同一对象（共用 helper 的穿透分支）。"""
    vl = OCRConfig(paddle_pipeline="vl")
    assert (
        _ocr_config_for_ppt_mode(vl, OCRConfig(paddle_pipeline="basic")) is vl
    )
