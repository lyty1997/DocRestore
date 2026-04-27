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


def _mock_response(
    content: str, *, finish_reason: str = "stop",
) -> SimpleNamespace:
    """构造 litellm response 形态的 mock；finish_reason 默认 stop。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )]
    )


def _make_refiner(
    response_content: str | Exception, *, finish_reason: str = "stop",
) -> CodeLLMRefiner:
    base = BaseLLMRefiner(LLMConfig())
    if isinstance(response_content, Exception):
        base._call_llm = AsyncMock(side_effect=response_content)  # type: ignore[method-assign]
    else:
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_response(
                response_content, finish_reason=finish_reason,
            ),
        )
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
    async def test_truncated_response_marked(self) -> None:
        """LLM 因 max_tokens 截断 → 不应误报 json_decode_error，应走 truncated 分支"""
        original = "int x = 0;\nint y = 0;"
        # 半截 JSON：finish_reason=length 时即使 raw 是合法 JSON 前缀也算截断
        half_json = '{"corrected_code": "int x = 0;\\nint y'
        result = await _make_refiner(
            half_json, finish_reason="length",
        ).refine(_make_source(original))
        assert result.refined_text == original
        assert "code.refine.truncated" in result.flags
        assert all(
            "json_decode_error" not in f for f in result.flags
        ), "截断必须独立分类，不能落到 json_decode_error 抹掉根因"

    @pytest.mark.asyncio
    async def test_max_tokens_explicitly_passed(self) -> None:
        """max_tokens 必须显式传给 _call_llm，不能依赖 provider 默认值。

        历史 bug：CodeLLMRefiner 不传 max_tokens → provider 默认 4096 →
        代码场景输出 ≈ corrected_code(≈输入) + corrections + unresolved
        三段 JSON 容易超 → 半截 JSON → 误报 json_decode_error。
        """
        original = "int x = 0;\nint y = 0;"
        response = json.dumps({
            "corrected_code": original,
            "corrections": [], "unresolved": [],
        })
        refiner = _make_refiner(response)
        await refiner.refine(_make_source(original))
        call_kwargs = refiner._base._call_llm.call_args.args[0]  # type: ignore[attr-defined]
        assert "max_tokens" in call_kwargs, (
            "CodeLLMRefiner 必须显式设 max_tokens，否则会因 provider 默认值"
            "（多数 4096）把代码场景的 JSON 输出截成半截"
        )
        assert call_kwargs["max_tokens"] >= 2048

    def test_estimate_max_tokens_bounds(self) -> None:
        """估算上限：下限 2048（短文件防 corrections 段挤破），上限 16384"""
        from docrestore.llm.code_refine import CodeLLMRefiner as _C
        assert _C._estimate_max_tokens("") == 2048
        assert _C._estimate_max_tokens("x = 0;") == 2048
        # 100K input → 1.6 × 25000 + 512 = 40512 → 封顶 16384
        assert _C._estimate_max_tokens("a" * 100000) == 16384
        # 10K input ≈ 2500 token；× 1.6 + 512 = 4512
        assert 4000 <= _C._estimate_max_tokens("a" * 10000) <= 5000

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


class TestRewriteMode:
    """rewrite 模式：允许重排 / 不强制行数守恒"""

    def _make_rewrite_refiner(
        self, response_content: str,
    ) -> CodeLLMRefiner:
        base = BaseLLMRefiner(LLMConfig())
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            return_value=_mock_response(response_content),
        )
        return CodeLLMRefiner(base, mode="rewrite")

    @pytest.mark.asyncio
    async def test_rewrite_accepts_different_line_count(self) -> None:
        """rewrite 模式输出行数与输入不同也接受"""
        original = "void foo(){int x=0;return;}"  # 1 行
        rewritten = "void foo() {\n  int x = 0;\n  return;\n}"  # 4 行
        response = json.dumps({
            "rewritten_code": rewritten,
            "summary": "重排格式 + 补缩进",
        })
        result = await self._make_rewrite_refiner(response).refine(
            _make_source(original),
        )
        assert result.refined_text == rewritten
        assert "code.refine.mode=rewrite" in result.flags
        assert any(f.startswith("code.refine.line_delta=") for f in result.flags)

    @pytest.mark.asyncio
    async def test_rewrite_empty_payload_falls_back(self) -> None:
        """rewrite_code 空 → 回退原文"""
        original = "int x = 0;"
        response = json.dumps({"rewritten_code": "", "summary": "noop"})
        result = await self._make_rewrite_refiner(response).refine(
            _make_source(original),
        )
        assert result.refined_text == original
        assert "code.refine.bad_payload" in result.flags

    @pytest.mark.asyncio
    async def test_rewrite_uses_rewrite_prompt_template(self) -> None:
        """rewrite 模式应该走 build_code_rewrite_prompt 而不是 refine"""
        original = "int x = 0;"
        response = json.dumps({"rewritten_code": original, "summary": "ok"})
        refiner = self._make_rewrite_refiner(response)
        await refiner.refine(_make_source(original))
        call_kwargs = refiner._base._call_llm.call_args.args[0]  # type: ignore[attr-defined]
        sys_msg = call_kwargs["messages"][0]["content"]
        # rewrite 提示词关键标识：含「重写」字样和 rewritten_code 字段
        assert "重写" in sys_msg
        assert "rewritten_code" in sys_msg

    def test_invalid_mode_rejected(self) -> None:
        """非法 mode 直接抛 ValueError"""
        with pytest.raises(ValueError, match="mode"):
            CodeLLMRefiner(BaseLLMRefiner(LLMConfig()), mode="bogus")
