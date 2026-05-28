# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式与 OCR 抽象契约的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from docrestore.ocr.base import OCR_RESULT_FILENAME
from docrestore.pipeline.config import CodeRestoreConfig, LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from tests.support.ocr_engine import FixtureOCREngine


@pytest.mark.asyncio
async def test_code_mode_requires_text_lines_contract(
    tmp_path: Path,
) -> None:
    """代码模式不绑定具体 OCR 引擎，但必须拿到 PageOCR.text_lines。"""
    image_dir = tmp_path / "imgs"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    (image_dir / "page1.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    ocr_dir = output_dir / "page1_OCR"
    ocr_dir.mkdir(parents=True)
    (ocr_dir / OCR_RESULT_FILENAME).write_text("print('hello')", encoding="utf-8")

    pipeline = Pipeline(PipelineConfig(llm=LLMConfig(model="")))
    pipeline.set_ocr_engine(FixtureOCREngine())

    with pytest.raises(RuntimeError, match="PageOCR.text_lines"):
        await pipeline.process_many(
            image_dir,
            output_dir,
            code=CodeRestoreConfig(enable=True),
        )
