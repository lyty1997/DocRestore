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
from docrestore.llm.code_refine import CodeRefineResult, CodeUnresolved
from docrestore.llm.code_repair import (
    _MAX_REPAIR_CHARS,
    _MAX_REPAIR_NO_IMPROVEMENT,
    _MAX_REPAIR_WINDOWS,
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
from docrestore.pipeline.config import LLMConfig, PIIConfig
from docrestore.privacy.guard import PIIGuard
from docrestore.privacy.redactor import EntityLexicon, PIIRedactor
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_context import LocalCodeContextProvider
from docrestore.processing.code_diagnostics import (
    CodeDiagnostic,
    CodeDiagnosticItem,
)
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

    def test_redact_masks_prompt_fields(self, tmp_path: Path) -> None:
        """#36：redact 非空时，repair context 拼进云端 prompt 的 file_path /
        related_snippets（含外部参考片段）/ diagnostics 均已脱敏，且仍是合法 JSON。
        """
        cfg = PIIConfig(enable=True)
        redactor = PIIRedactor(cfg)

        def redact(text: str) -> str:
            return redactor.redact_regex_only(text)[0]

        # 外部参考源码树放含邮箱的片段（context_root 外发，vector ③b）
        ref = tmp_path / "src" / "foo.py"
        ref.parent.mkdir()
        ref.write_text(
            "def helper_symbol():\n"
            "    # owner: leaker@corp.example\n"
            "    return 1\n",
            encoding="utf-8",
        )
        source = _source("def run():\n    return helper_symbol(")
        source.path = "users/13800138000/foo.py"  # path 里塞结构化 PII（手机号）
        # 诊断 summary/message 模拟编译器 caret 回显含邮箱的源码行（发现的额外泄漏）
        diag = CodeDiagnostic(
            path="users/13800138000/foo.py",
            language="python", status="syntax_dirty", category="syntax",
            summary="error: stray 'maintainer@corp.example'",
            failing_lines=[2], syntax_errors=1,
            items=[CodeDiagnosticItem(
                line=2, message="near contact@corp.example",
            )],
        )
        contexts = build_repair_contexts(
            source, [diag], related_sources=[],
            context_provider=LocalCodeContextProvider(tmp_path),
            window_radius=1, redact=redact,
        )
        prompt_json = contexts[0].to_prompt_json()
        # 明文 PII 一律不得出现在送云端的 prompt 里（file_path / 片段 / 诊断三处）
        for leaked in (
            "leaker@corp.example", "maintainer@corp.example",
            "contact@corp.example", "13800138000",
        ):
            assert leaked not in prompt_json
        # 占位符出现（派生自配置），且产物仍是合法 JSON（先脱后序列化的保证）
        assert cfg.email_placeholder in prompt_json
        assert cfg.phone_placeholder in prompt_json
        json.loads(prompt_json)  # 不抛 = JSON 结构完好

    def test_no_redact_leaves_prompt_fields_raw(self, tmp_path: Path) -> None:
        """对照：redact=None（未开 PII）→ 外部参考片段保持原文，行为不变。"""
        ref = tmp_path / "src" / "foo.py"
        ref.parent.mkdir()
        ref.write_text(
            "def helper_symbol():\n"
            "    # owner: leaker@corp.example\n"
            "    return 1\n",
            encoding="utf-8",
        )
        source = _source("def run():\n    return helper_symbol(")
        contexts = build_repair_contexts(
            source, [_diag(2)], related_sources=[],
            context_provider=LocalCodeContextProvider(tmp_path),
            window_radius=1,
        )
        assert "leaker@corp.example" in contexts[0].related_snippets[0]


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


def _no_patch(line: int, note: str = "unclear") -> SimpleNamespace:
    """构造一个「无 patch」的 repair 响应（窗口不落 patch，算一次无改善）。"""
    return _response(json.dumps({
        "plan": "cannot decide",
        "dependency_assessment": "weak",
        "patch": None,
        "unresolved": [{"line": line, "context": "x", "note": note}],
    }))


def _lines_source(failing: list[int], *, span: int = 2) -> SourceFile:
    """构造行数足够、在 ``failing`` 各行附近留窗口的多窗口源（每行 ``vN = N``）。"""
    last = max(failing) + span
    text = "\n".join(f"v{i} = {i}" for i in range(1, last + 1))
    return _source(text)


class TestRepairConvergenceBounds:
    """#94：repair 窗口循环收敛兜底（窗口预算 / 连续无改善早停 / 超大文件熔断）。"""

    @pytest.mark.asyncio
    async def test_early_stop_on_consecutive_no_improvement(self) -> None:
        """连续 N 个窗口都不落 patch → 早停，只发 N 次 LLM 调用、回退原文。"""
        failing = [1, 5, 9, 13, 17]  # 5 个不相邻窗口（radius=1 不合并）
        source = _lines_source(failing)
        base = BaseLLMRefiner(LLMConfig())
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_no_patch(line) for line in failing],
        )
        result = await DiagnosticCodeRepairer(base, window_radius=1).repair(
            source, [_diag(line) for line in failing],
        )
        # 早停在第 _MAX_REPAIR_NO_IMPROVEMENT 个窗口：调用数恰为阈值、不跑满 5 个。
        assert base._call_llm.call_count == _MAX_REPAIR_NO_IMPROVEMENT
        assert result.refined_text == source.merged_text
        assert any(
            flag.startswith("code.repair.early_stop=") for flag in result.flags
        )
        # 未处理窗口的范围作为 unresolved 透出（不埋雷）。
        assert any("repair 预算未修复" in u.note for u in result.unresolved)

    @pytest.mark.asyncio
    async def test_applied_patch_resets_no_improvement_counter(self) -> None:
        """中间窗口落 patch 会清零计数 → 不触发早停、跑满所有窗口。"""
        failing = [1, 5, 9, 13, 17]
        source = _lines_source(failing)
        apply_win = _response(json.dumps({
            "plan": "fix",
            "dependency_assessment": "local",
            "patch": {
                "start_line": 9, "end_line": 9,
                "replacement_lines": ["v9 = 99"],
            },
            "unresolved": [],
        }))
        base = BaseLLMRefiner(LLMConfig())
        # 模式 no, no, apply, no, no：计数 1,2,0,1,2，从不达 3。
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                _no_patch(1), _no_patch(5), apply_win,
                _no_patch(13), _no_patch(17),
            ],
        )
        result = await DiagnosticCodeRepairer(base, window_radius=1).repair(
            source, [_diag(line) for line in failing],
        )
        assert base._call_llm.call_count == len(failing)
        assert not any(
            flag.startswith("code.repair.early_stop=") for flag in result.flags
        )

    @pytest.mark.asyncio
    async def test_window_budget_caps_and_surfaces_skipped(self) -> None:
        """窗口数超预算 → 计 window_cap flag、被丢弃窗口作为 unresolved 透出。"""
        # 步长 4：radius=1 时相邻窗口间隔须 ≥4 才不合并（否则 14 行塌成 1 窗口）。
        failing = list(range(1, 1 + 4 * (_MAX_REPAIR_WINDOWS + 2), 4))
        assert len(failing) > _MAX_REPAIR_WINDOWS
        source = _lines_source(failing)
        base = BaseLLMRefiner(LLMConfig())
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_no_patch(line) for line in failing],
        )
        result = await DiagnosticCodeRepairer(base, window_radius=1).repair(
            source, [_diag(line) for line in failing],
        )
        # 预算上限独立于早停：超出的窗口必被计 window_cap 并透出。
        assert any(
            flag.startswith("code.repair.window_cap=") for flag in result.flags
        )
        # 即便有十几个窗口，调用数也被早停/预算双重封顶，绝不爆炸。
        assert base._call_llm.call_count <= _MAX_REPAIR_WINDOWS
        assert any("repair 预算未修复" in u.note for u in result.unresolved)

    @pytest.mark.asyncio
    async def test_oversized_file_short_circuits_without_llm(self) -> None:
        """正文超 _MAX_REPAIR_CHARS → 直接熔断，零 LLM 调用、回退原文、失败行透出。"""
        big = "\n".join(["x = 1"] * (_MAX_REPAIR_CHARS // 5 + 100))
        assert len(big) > _MAX_REPAIR_CHARS
        source = _source(big)
        base = _base(_no_patch(1).choices[0].message.content)
        result = await DiagnosticCodeRepairer(base, window_radius=1).repair(
            source, [_diag(1)],
        )
        base._call_llm.assert_not_called()  # type: ignore[attr-defined]
        assert result.refined_text == source.merged_text
        assert any(
            flag.startswith("code.repair.skipped_oversized=")
            for flag in result.flags
        )
        assert result.unresolved

    @pytest.mark.asyncio
    async def test_audit_skips_oversized_file(self) -> None:
        """audit 与 repair 共用熔断闸口：超大文件零 LLM 调用、回退原文、计 flag。"""
        big = "\n".join(["x = 1"] * (_MAX_REPAIR_CHARS // 5 + 100))
        assert len(big) > _MAX_REPAIR_CHARS
        source = _source(big)
        base = _base(_no_patch(1).choices[0].message.content)
        result = await CodeConsistencyAuditor(base).audit(
            source, [_diag(1)],
            previous_result=CodeRefineResult(refined_text=big),
        )
        base._call_llm.assert_not_called()  # type: ignore[attr-defined]
        assert result.refined_text == big
        assert "code.audit.skipped_oversized" in result.flags

    @pytest.mark.asyncio
    async def test_progress_cb_reports_each_window(self) -> None:
        """progress_cb 每个处理过的窗口上报一次 (window_index, window_total)。"""
        failing = [1, 5]  # 2 个窗口 < 早停阈值，全部处理
        source = _lines_source(failing)
        base = BaseLLMRefiner(LLMConfig())
        base._call_llm = AsyncMock(  # type: ignore[method-assign]
            side_effect=[_no_patch(line) for line in failing],
        )
        seen: list[tuple[int, int]] = []
        await DiagnosticCodeRepairer(base, window_radius=1).repair(
            source, [_diag(line) for line in failing],
            progress_cb=lambda w, total: seen.append((w, total)),
        )
        assert seen == [(1, 2), (2, 2)]


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

    def test_unresolved_items_redacted(self) -> None:
        """#67：unresolved 的 context/note 自由文本送云前脱敏（结构化 + 实体）。

        闸口只兜底实体；此处字段级补结构化（手机/邮箱）——覆盖闸口够不到的
        unresolved 自由文本。redact 用带 lexicon 的 redact_for_cloud（同生产口径）。
        """
        cfg = PIIConfig(enable=True, redact_person_name=True)
        guard = PIIGuard(cfg)
        lexicon = EntityLexicon(person_names=("张三",), org_names=())

        def redact(text: str) -> str:
            return guard.redact_for_cloud(text, lexicon)

        source = _source("def run():\n    return 1\n")
        prev = CodeRefineResult(
            refined_text=source.merged_text,
            unresolved=[
                CodeUnresolved(
                    line=2,
                    context="联系 张三 13800138000",
                    note="作者 leaker@corp.example",
                ),
            ],
        )
        context = build_consistency_audit_context(
            source, [], previous_result=prev,
            related_sources=[], redact=redact,
        )
        prompt_json = context.to_prompt_json()
        for leaked in ("张三", "13800138000", "leaker@corp.example"):
            assert leaked not in prompt_json
        assert cfg.person_name_placeholder in prompt_json  # 实体确被替
        json.loads(prompt_json)  # 仍是合法 JSON

    def test_unresolved_items_raw_without_redact(self) -> None:
        """对照：redact=None（未开 PII）→ unresolved 原文保留，行为不变。"""
        source = _source("def run():\n    return 1\n")
        prev = CodeRefineResult(
            refined_text=source.merged_text,
            unresolved=[
                CodeUnresolved(line=2, context="联系 张三", note="x"),
            ],
        )
        context = build_consistency_audit_context(
            source, [], previous_result=prev, related_sources=[],
        )
        assert "张三" in context.to_prompt_json()

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
