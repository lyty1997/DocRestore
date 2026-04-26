# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""CodeLLMRefiner 单测（AGE-8 Phase 3.1）"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from docrestore.llm.base import BaseLLMRefiner
from docrestore.llm.code_refine import (
    CodeLLMRefiner,
    CodeRefineResult,
    _extract_json_payload,
)
from docrestore.pipeline.config import LLMConfig
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import PageColumn, SourceFile
from docrestore.processing.ide_meta_extract import IDEMeta


def _make_source(text: str, *, path: str = "src/foo.cc",
                 language: str = "cpp") -> SourceFile:
    line_count = text.count("\n") + 1 if text else 0
    page = PageColumn(
        page_stem="DSC1", column_index=0,
        meta=IDEMeta(column_index=0, filename="foo.cc",
                     path=path, language=language),
        column=CodeColumn(
            column_index=0, bbox=(0, 0, 1, 1), code_text=text,
            lines=[CodeLine(line_no=1, text=text, indent=0)],
            char_width=12.0, avg_line_height=30,
        ),
    )
    return SourceFile(
        path=path, filename="foo.cc", language=language,
        pages=[page], merged_text=text,
        line_count=line_count, line_no_range=(1, line_count or 1),
    )


def _mock_response(content: str) -> SimpleNamespace:
    """构造 litellm response 形态的 mock"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _make_refiner(response_content: str | Exception) -> CodeLLMRefiner:
    base = BaseLLMRefiner(LLMConfig())
    if isinstance(response_content, Exception):
        base._call_llm = AsyncMock(side_effect=response_content)  # type: ignore[method-assign]
    else:
        base._call_llm = AsyncMock(return_value=_mock_response(response_content))  # type: ignore[method-assign]
    return CodeLLMRefiner(base)


# ---------- 测试 ----------

class TestExtractJsonPayload:
    def test_plain_json(self) -> None:
        assert _extract_json_payload('{"a": 1}') == '{"a": 1}'

    def test_code_fence_json(self) -> None:
        raw = '```json\n{"a": 1}\n```'
        assert _extract_json_payload(raw) == '{"a": 1}'

    def test_with_prefix_text(self) -> None:
        raw = 'Here is JSON: {"a": 1} thanks'
        assert _extract_json_payload(raw) == '{"a": 1}'


class TestRefineHappyPath:
    @pytest.mark.asyncio
    async def test_normal_correction(self) -> None:
        """正常 LLM 输出 → 解析 corrections"""
        original = "int x = 0;\nint y = O;"
        refined = "int x = 0;\nint y = 0;"  # 同行数
        response = json.dumps({
            "corrected_code": refined,
            "corrections": [
                {"line": 2, "before": "O", "after": "0", "reason": "OCR O→0"},
            ],
            "unresolved": [],
        })
        refiner = _make_refiner(response)
        result = await refiner.refine(_make_source(original))
        assert isinstance(result, CodeRefineResult)
        assert result.refined_text == refined
        assert len(result.corrections) == 1
        assert result.corrections[0].before == "O"
        assert result.corrections[0].after == "0"
        assert "code.refine.applied=1" in result.flags

    @pytest.mark.asyncio
    async def test_unresolved_recorded(self) -> None:
        original = "x = Y天;\n"
        response = json.dumps({
            "corrected_code": original,
            "corrections": [],
            "unresolved": [
                {"line": 1, "context": "Y天", "note": "unclear bracket"},
            ],
        })
        result = await _make_refiner(response).refine(_make_source(original))
        assert len(result.unresolved) == 1
        assert "Y天" in result.unresolved[0].context

    @pytest.mark.asyncio
    async def test_code_fence_response(self) -> None:
        original = "x = 0;"
        response = (
            '```json\n'
            + json.dumps({"corrected_code": original, "corrections": [],
                          "unresolved": []})
            + '\n```'
        )
        result = await _make_refiner(response).refine(_make_source(original))
        assert result.refined_text == original


class TestRefineSafetyGuards:
    @pytest.mark.asyncio
    async def test_line_count_mismatch_rejected(self) -> None:
        """LLM 加了一行 → 安全校验失败回退原文"""
        original = "int x = 0;\nint y = 0;"  # 2 行
        cheating = (
            '{"corrected_code": "int x = 0;\\nint y = 0;\\n// extra",'
            ' "corrections": [], "unresolved": []}'
        )
        result = await _make_refiner(cheating).refine(_make_source(original))
        assert result.refined_text == original   # 回退
        assert any("line_count_mismatch" in f for f in result.flags)

    @pytest.mark.asyncio
    async def test_invalid_json_rejected(self) -> None:
        original = "x = 0;"
        result = await _make_refiner("not json").refine(_make_source(original))
        assert result.refined_text == original
        assert any("json_decode_error" in f for f in result.flags)

    @pytest.mark.asyncio
    async def test_llm_error_falls_back(self) -> None:
        original = "x = 0;"
        result = await _make_refiner(
            RuntimeError("503 service unavail"),
        ).refine(_make_source(original))
        assert result.refined_text == original
        assert any("llm_error" in f for f in result.flags)

    @pytest.mark.asyncio
    async def test_empty_choices_falls_back(self) -> None:
        original = "x = 0;"
        base = BaseLLMRefiner(LLMConfig())
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(choices=[]),
        )
        result = await CodeLLMRefiner(base).refine(_make_source(original))
        assert result.refined_text == original
        assert "code.refine.empty_choices" in result.flags

    @pytest.mark.asyncio
    async def test_empty_input_skip(self) -> None:
        result = await _make_refiner('{"corrected_code": ""}').refine(
            _make_source(""),
        )
        assert result.refined_text == ""
        assert "code.refine.empty_input" in result.flags

    @pytest.mark.asyncio
    async def test_corrupt_corrections_dropped(self) -> None:
        """corrections 字段含非法项 → 跳过非法项"""
        original = "x = 0;"
        response = json.dumps({
            "corrected_code": original,
            "corrections": [
                {"line": "bad", "before": 1, "after": [], "reason": None},
                {"line": 1, "before": "O", "after": "0"},
            ],
            "unresolved": [],
        })
        result = await _make_refiner(response).refine(_make_source(original))
        # 第一项 line=int(non-numeric str) 失败被跳过；第二项保留
        assert any(c.before == "O" for c in result.corrections)
