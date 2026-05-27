# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""诊断驱动 scoped code repair 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from docrestore.llm.base import BaseLLMRefiner
from docrestore.llm.code_refine import CodeRefineResult
from docrestore.llm.code_repair import (
    CodeEditRange,
    CodeConsistencyAuditor,
    DiagnosticCodeRepairer,
    ScopedPatch,
    apply_scoped_patch,
    build_consistency_audit_context,
    build_repair_contexts,
    parse_consistency_audit_response,
    parse_repair_response,
)
from docrestore.pipeline.config import LLMConfig
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_context import LocalCodeContextProvider
from docrestore.processing.code_diagnostics import CodeDiagnostic
from docrestore.processing.code_file_grouping import PageColumn, SourceFile
from docrestore.processing.ide_meta_extract import IDEMeta, PathCandidate


def _source(text: str, *, language: str = "python") -> SourceFile:
    line_count = text.count("\n") + 1 if text else 0
    meta = IDEMeta(
        column_index=0,
        filename="foo.py",
        path="src/foo.py",
        language=language,
        path_candidates=[
            PathCandidate(
                path="src/foo.py",
                filename="foo.py",
                language=language,
                source="breadcrumb",
                confidence=0.9,
                raw_text="src > foo.py",
            )
        ],
    )
    column = CodeColumn(
        column_index=0,
        bbox=(0, 0, 1, 1),
        code_text=text,
        lines=[CodeLine(line_no=1, text=text, indent=0)],
        char_width=10,
        avg_line_height=20,
    )
    page = PageColumn("page1", 0, meta, column)
    return SourceFile(
        path="src/foo.py",
        filename="foo.py",
        language=language,
        pages=[page],
        merged_text=text,
        line_count=line_count,
        line_no_range=(1, line_count or 1),
    )


def _diag(line: int = 2) -> CodeDiagnostic:
    return CodeDiagnostic(
        path="src/foo.py",
        language="python",
        status="syntax_dirty",
        category="syntax",
        summary="SyntaxError",
        failing_lines=[line],
        syntax_errors=1,
    )


def _response(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content),
        finish_reason=finish_reason,
    )])


def _base(content: str, finish_reason: str = "stop") -> BaseLLMRefiner:
    base = BaseLLMRefiner(LLMConfig())
    base._call_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=_response(content, finish_reason),
    )
    return base


class TestRepairContext:
    def test_context_contains_edit_range_and_readonly_metadata(self) -> None:
        source = _source("def run():\n    return (")
        contexts = build_repair_contexts(
            source, [_diag(2)], related_sources=[], window_radius=1,
        )
        assert len(contexts) == 1
        context = contexts[0]
        assert context.edit_range == CodeEditRange(1, 2)
        assert context.local_lines == ["1: def run():", "2:     return ("]
        assert context.source_pages == ["page1.col0"]
        assert context.path_candidates[0]["path"] == "src/foo.py"
        assert "readonly context must not be modified" in context.constraints

    def test_context_includes_reference_snippet(
        self, tmp_path: Path,
    ) -> None:
        ref = tmp_path / "src" / "foo.py"
        ref.parent.mkdir()
        ref.write_text("def helper_symbol():\n    return 1\n", encoding="utf-8")
        source = _source("def run():\n    return helper_symbol(")
        contexts = build_repair_contexts(
            source,
            [_diag(2)],
            related_sources=[],
            context_provider=LocalCodeContextProvider(tmp_path),
            window_radius=1,
        )
        assert "reference: src/foo.py" in contexts[0].related_snippets[0]
        assert "helper_symbol" in contexts[0].related_snippets[0]


class TestScopedPatch:
    def test_patch_inside_edit_range_applied(self) -> None:
        text = "a\nbad\nc"
        patch = ScopedPatch(2, 2, ["fixed"])
        result = apply_scoped_patch(text, CodeEditRange(2, 2), patch)
        assert result == "a\nfixed\nc"

    def test_patch_outside_edit_range_rejected(self) -> None:
        text = "a\nbad\nc"
        patch = ScopedPatch(1, 2, ["x"])
        assert apply_scoped_patch(text, CodeEditRange(2, 2), patch) is None


class TestRepairResponse:
    def test_parse_rejects_out_of_scope_patch(self) -> None:
        context = build_repair_contexts(
            _source("a\nbad\nc"), [_diag(2)], related_sources=[], window_radius=0,
        )[0]
        payload = json.dumps({
            "plan": "fix",
            "dependency_assessment": "local",
            "patch": {
                "start_line": 1,
                "end_line": 2,
                "replacement_lines": ["x"],
            },
            "unresolved": [],
        })
        attempt = parse_repair_response(_response(payload), context)
        assert attempt.patch is None
        assert "code.repair.reject_scope" in attempt.flags

    def test_truncated_response_falls_back(self) -> None:
        context = build_repair_contexts(
            _source("a\nbad\nc"), [_diag(2)], related_sources=[], window_radius=0,
        )[0]
        attempt = parse_repair_response(_response("{", "length"), context)
        assert attempt.patch is None
        assert "code.repair.truncated" in attempt.flags


class TestDiagnosticCodeRepairer:
    @pytest.mark.asyncio
    async def test_repair_applies_scoped_patch_and_rediagnoses(self) -> None:
        source = _source("def run():\n    return (")
        payload = json.dumps({
            "plan": "close call",
            "dependency_assessment": "local",
            "patch": {
                "start_line": 2,
                "end_line": 2,
                "replacement_lines": ["    return 1"],
            },
            "unresolved": [],
        })
        result = await DiagnosticCodeRepairer(
            _base(payload), window_radius=1,
        ).repair(source, [_diag(2)])
        assert result.refined_text == "def run():\n    return 1"
        assert "code.repair.windows=1" in result.flags

    @pytest.mark.asyncio
    async def test_unresolved_without_patch_keeps_original(self) -> None:
        source = _source("def run():\n    return (")
        payload = json.dumps({
            "plan": "cannot decide",
            "dependency_assessment": "needs more context",
            "patch": None,
            "unresolved": [{"line": 2, "context": "return (", "note": "unclear"}],
        })
        result = await DiagnosticCodeRepairer(
            _base(payload), window_radius=1,
        ).repair(source, [_diag(2)])
        assert result.refined_text == source.merged_text
        assert result.unresolved[0].note == "unclear"
        assert "code.repair.unresolved" in result.flags

    @pytest.mark.asyncio
    async def test_repair_remaps_later_window_after_line_count_change(
        self,
    ) -> None:
        """前一窗口 patch 改变行数后，后续窗口 patch 应按偏移落到正确行（B7 C2）。"""
        source = _source("x = 1\ny = 2\nz = 3\na = 4\nb = 5\nc = 6\nd = 7")
        win1 = json.dumps({
            "plan": "merge",
            "dependency_assessment": "local",
            "patch": {
                "start_line": 1,
                "end_line": 2,
                "replacement_lines": ["x = 1  # merged"],
            },
            "unresolved": [],
        })
        win2 = json.dumps({
            "plan": "fix c",
            "dependency_assessment": "local",
            "patch": {
                "start_line": 6,
                "end_line": 6,
                "replacement_lines": ["c = 60"],
            },
            "unresolved": [],
        })
        base = BaseLLMRefiner(LLMConfig())
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_response(win1), _response(win2)],
        )
        result = await DiagnosticCodeRepairer(base, window_radius=1).repair(
            source, [_diag(2), _diag(6)],
        )
        # 第一个窗口删掉一行后整体上移 1 行；第二个窗口的 patch（原文第 6 行
        # "c = 6"）应被平移到 current 第 5 行命中，而不是错改 "d = 7"。
        assert result.refined_text.split("\n") == [
            "x = 1  # merged", "z = 3", "a = 4", "b = 5", "c = 60", "d = 7",
        ]

    @pytest.mark.asyncio
    async def test_repair_rejects_truncating_patch(self) -> None:
        """替换行数骤减、丢失过半窗口内容的 patch 按截断拒绝、回退原文（B7 C4）。"""
        source = _source("\n".join(f"line{i}" for i in range(1, 12)))
        payload = json.dumps({
            "plan": "rewrite",
            "dependency_assessment": "local",
            "patch": {
                "start_line": 1,
                "end_line": 10,
                "replacement_lines": ["a", "b"],
            },
            "unresolved": [],
        })
        result = await DiagnosticCodeRepairer(
            _base(payload), window_radius=8,
        ).repair(source, [_diag(5)])
        assert result.refined_text == source.merged_text
        assert "code.repair.reject_truncation" in result.flags


class TestConsistencyAudit:
    def test_audit_context_uses_editable_and_readonly_excerpts(self) -> None:
        source = _source("H0ST = 1\nHOST = 2\nprint(HOST)\n")
        context = build_consistency_audit_context(
            source,
            [],
            previous_result=CodeRefineResult(
                refined_text=source.merged_text,
                flags=["code.repair.applied=1"],
            ),
            related_sources=[],
            excerpt_radius=1,
        )
        assert context.editable_ranges
        assert any(
            "read_only_excerpts are evidence only" in item
            for item in context.constraints
        )
        assert context.repeated_ocr_confusions[0]["variants"] == ["H0ST", "HOST"]
        editable_lines = {
            line_no
            for edit_range in context.editable_ranges
            for line_no in range(edit_range.start_line, edit_range.end_line + 1)
        }
        assert all(
            int(excerpt.split(":", 1)[0]) not in editable_lines
            for excerpt in context.read_only_excerpts
        )

    def test_parse_keeps_readonly_patch_for_later_rejection(self) -> None:
        source = _source("a = 1\nb = 2\nbad = 3\nc = 4\nz = 5")
        context = build_consistency_audit_context(
            source,
            [_diag(3)],
            previous_result=CodeRefineResult(refined_text=source.merged_text),
            related_sources=[],
        )
        payload = json.dumps({
            "plan": "fix outside",
            "patches": [{
                "start_line": 1,
                "end_line": 1,
                "replacement_lines": ["a = 2"],
                "evidence": "not editable",
            }],
            "candidate_ranges": [],
            "unresolved": [],
        })
        attempt = parse_consistency_audit_response(_response(payload), context)
        assert attempt.patches[0].patch.start_line == 1

    @pytest.mark.asyncio
    async def test_audit_applies_patch_inside_editable_range(self) -> None:
        source = _source("H0ST = 1\nHOST = 2\nprint(HOST)")
        payload = json.dumps({
            "plan": "normalize symbol",
            "patches": [{
                "start_line": 1,
                "end_line": 1,
                "replacement_lines": ["HOST = 1"],
                "evidence": "matches line 2",
            }],
            "candidate_ranges": [],
            "unresolved": [],
        })
        result = await CodeConsistencyAuditor(_base(payload)).audit(
            source,
            [],
            previous_result=CodeRefineResult(refined_text=source.merged_text),
            related_sources=[],
        )
        assert result.refined_text.startswith("HOST = 1\nHOST = 2")
        assert "code.audit.patches=1" in result.flags

    @pytest.mark.asyncio
    async def test_audit_rejects_readonly_patch(self) -> None:
        source = _source("a = 1\nb = 2\nbad = 3\nc = 4\nz = 5")
        payload = json.dumps({
            "plan": "bad scope",
            "patches": [{
                "start_line": 1,
                "end_line": 1,
                "replacement_lines": ["a = 2"],
                "evidence": "outside editable range",
            }],
            "candidate_ranges": [],
            "unresolved": [],
        })
        result = await CodeConsistencyAuditor(_base(payload)).audit(
            source,
            [_diag(3)],
            previous_result=CodeRefineResult(refined_text=source.merged_text),
            related_sources=[],
        )
        assert result.refined_text == source.merged_text
        assert "code.audit.reject_readonly_patch" in result.flags

    @pytest.mark.asyncio
    async def test_audit_candidate_range_without_patch(self) -> None:
        source = _source("a = 1\nbad = 2\nc = 3")
        payload = json.dumps({
            "plan": "needs larger range",
            "patches": [],
            "candidate_ranges": [{
                "start_line": 1,
                "end_line": 3,
                "reason": "block imbalance",
            }],
            "unresolved": [],
        })
        result = await CodeConsistencyAuditor(_base(payload)).audit(
            source,
            [_diag(2)],
            previous_result=CodeRefineResult(refined_text=source.merged_text),
            related_sources=[],
        )
        assert result.refined_text == source.merged_text
        assert "code.audit.candidate_ranges=1" in result.flags

    @pytest.mark.asyncio
    async def test_audit_rejects_diagnostic_worse(self) -> None:
        source = _source("H0ST = 1\nHOST = 2")
        payload = json.dumps({
            "plan": "bad patch",
            "patches": [{
                "start_line": 1,
                "end_line": 1,
                "replacement_lines": ["H0ST ="],
                "evidence": "bad",
            }],
            "candidate_ranges": [],
            "unresolved": [],
        })
        result = await CodeConsistencyAuditor(_base(payload)).audit(
            source,
            [],
            previous_result=CodeRefineResult(refined_text=source.merged_text),
            related_sources=[],
        )
        assert result.refined_text == source.merged_text
        assert "code.audit.reject_diagnostic_worse" in result.flags

    @pytest.mark.asyncio
    async def test_audit_remaps_multiple_patches_after_line_shift(self) -> None:
        """多个 audit patch 顺序应用时按 start_line 升序 + 偏移定位（B7 C2/C3）。"""
        source = _source("H0ST = 1\nb = 2\nc = 3\nd = 4\nHOST = 5")
        # 故意把 patch 乱序给出：第二个改原文第 5 行，第一个把前两行合并成一行。
        payload = json.dumps({
            "plan": "normalize",
            "patches": [
                {
                    "start_line": 5,
                    "end_line": 5,
                    "replacement_lines": ["HOST = 50"],
                    "evidence": "line5",
                },
                {
                    "start_line": 1,
                    "end_line": 2,
                    "replacement_lines": ["HOST = 1"],
                    "evidence": "merge top",
                },
            ],
            "candidate_ranges": [],
            "unresolved": [],
        })
        result = await CodeConsistencyAuditor(_base(payload)).audit(
            source,
            [],
            previous_result=CodeRefineResult(refined_text=source.merged_text),
            related_sources=[],
        )
        # 合并前两行后整体上移 1；第 5 行 patch 应平移到 current 第 4 行命中。
        assert result.refined_text.split("\n") == [
            "HOST = 1", "c = 3", "d = 4", "HOST = 50",
        ]
        assert "code.audit.patches=2" in result.flags
