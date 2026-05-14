# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""诊断驱动的代码 scoped repair。

该模块实现“编辑范围小、只读上下文大”的 LLM 修复基础能力。LLM 只能返回
落在 edit_range 内的 patch；同文件 outline、来源页、路径候选和相关片段
只作为 prompt 上下文，不允许被 patch 修改。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from docrestore.llm.base import BaseLLMRefiner
from docrestore.llm.code_refine import CodeRefineResult, CodeUnresolved
from docrestore.llm.prompts import build_code_repair_prompt
from docrestore.processing.code_diagnostics import (
    CodeDiagnostic,
    CodeDiagnosticRunner,
    diagnose_text,
)

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import SourceFile

logger = logging.getLogger(__name__)

_SYMBOL_RE = re.compile(
    r"^\s*(?:class|def|function|func|struct|enum|interface|type|impl|"
    r"namespace)\s+([A-Za-z_][A-Za-z0-9_]*)"
)


@dataclass(frozen=True)
class CodeEditRange:
    """允许 LLM 修改的闭区间行号。"""

    start_line: int
    end_line: int


@dataclass(frozen=True)
class CodeRepairContext:
    """诊断驱动 scoped repair prompt 上下文。"""

    file_path: str
    language: str | None
    edit_range: CodeEditRange
    local_lines: list[str]
    enclosing_symbols: list[str] = field(default_factory=list)
    file_outline: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, object]] = field(default_factory=list)
    related_snippets: list[str] = field(default_factory=list)
    path_candidates: list[dict[str, object]] = field(default_factory=list)
    source_pages: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_prompt_json(self) -> str:
        """序列化为 prompt JSON。"""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class ScopedPatch:
    """LLM 返回的 scoped patch。"""

    start_line: int
    end_line: int
    replacement_lines: list[str]


@dataclass(frozen=True)
class CodeRepairAttempt:
    """单个窗口的修复尝试结果。"""

    context: CodeRepairContext
    patch: ScopedPatch | None = None
    plan: str = ""
    dependency_assessment: str = ""
    unresolved: list[CodeUnresolved] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


class DiagnosticCodeRepairer:
    """基于诊断行生成小窗口并调用 LLM scoped patch。"""

    def __init__(
        self,
        base: BaseLLMRefiner,
        *,
        diagnostic_runner: CodeDiagnosticRunner | None = None,
        window_radius: int = 8,
    ) -> None:
        self._base = base
        self._diagnostic_runner = diagnostic_runner
        self._window_radius = window_radius

    async def repair(
        self,
        source: SourceFile,
        diagnostics: list[CodeDiagnostic],
        *,
        related_sources: list[SourceFile] | None = None,
    ) -> CodeRefineResult:
        """按诊断窗口修复 SourceFile；失败或恶化时回退原文。"""
        contexts = build_repair_contexts(
            source,
            diagnostics,
            related_sources=related_sources or [],
            window_radius=self._window_radius,
        )
        if not contexts:
            return CodeRefineResult(
                refined_text=source.merged_text,
                flags=["code.repair.no_windows"],
            )

        original = source.merged_text
        current = original
        attempts: list[CodeRepairAttempt] = []
        original_score = _diagnostic_score(diagnostics)
        for context in contexts:
            attempt = await self._repair_one_window(current, source, context)
            attempts.append(attempt)
            if attempt.patch is None or any(
                flag.startswith("code.repair.reject") for flag in attempt.flags
            ):
                continue
            patched = apply_scoped_patch(current, context.edit_range, attempt.patch)
            if patched is None:
                attempts[-1] = _with_flag(attempt, "code.repair.reject_scope")
                continue
            post = diagnose_text(
                path=source.path,
                language=source.language,
                text=patched,
                runner=self._diagnostic_runner,
            )
            if _diagnostic_score([post]) > original_score:
                attempts[-1] = _with_flag(
                    attempt, "code.repair.reject_diagnostic_worse",
                )
                continue
            current = patched

        if current == original:
            return CodeRefineResult(
                refined_text=original,
                unresolved=_collect_unresolved(attempts),
                flags=_collect_flags(attempts) or ["code.repair.no_change"],
            )

        return CodeRefineResult(
            refined_text=current,
            unresolved=_collect_unresolved(attempts),
            flags=[
                f"code.repair.windows={len(contexts)}",
                f"code.repair.applied={sum(1 for a in attempts if a.patch)}",
                *_collect_flags(attempts),
            ],
        )

    async def _repair_one_window(
        self,
        current_text: str,
        source: SourceFile,
        context: CodeRepairContext,
    ) -> CodeRepairAttempt:
        del current_text, source
        messages = build_code_repair_prompt(context.to_prompt_json())
        kwargs = self._base._build_kwargs(messages)
        kwargs["max_tokens"] = 4096
        try:
            response = await self._base._call_llm(kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CodeRepair LLM 调用失败，跳过窗口: %s", exc)
            return CodeRepairAttempt(
                context=context,
                flags=[f"code.repair.llm_error={type(exc).__name__}"],
            )
        return parse_repair_response(response, context)


def build_repair_contexts(
    source: SourceFile,
    diagnostics: list[CodeDiagnostic],
    *,
    related_sources: list[SourceFile],
    window_radius: int = 8,
) -> list[CodeRepairContext]:
    """根据诊断失败行生成 scoped repair contexts。"""
    failing_lines = sorted({
        line
        for diagnostic in diagnostics
        if diagnostic.status == "syntax_dirty"
        for line in diagnostic.failing_lines
        if line > 0
    })
    if not failing_lines:
        return []
    line_count = max(1, source.merged_text.count("\n") + 1)
    ranges = _merge_line_windows(failing_lines, line_count, window_radius)
    lines = source.merged_text.split("\n")
    outline = _file_outline(lines)
    path_candidates = _path_candidates(source)
    source_pages = [
        f"{page.page_stem}.col{page.column_index}" for page in source.pages
    ]
    return [
        CodeRepairContext(
            file_path=source.path,
            language=source.language,
            edit_range=edit_range,
            local_lines=_numbered_lines(lines, edit_range),
            enclosing_symbols=_enclosing_symbols(lines, edit_range.start_line),
            file_outline=outline,
            diagnostics=[
                diagnostic.to_index_dict() for diagnostic in diagnostics
            ],
            related_snippets=_related_snippets(source, related_sources),
            path_candidates=path_candidates,
            source_pages=source_pages,
            constraints=[
                "patch may only replace lines inside edit_range",
                "readonly context must not be modified",
                "do not invent business logic; use unresolved when evidence is weak",
            ],
        )
        for edit_range in ranges
    ]


def apply_scoped_patch(
    text: str,
    edit_range: CodeEditRange,
    patch: ScopedPatch,
) -> str | None:
    """应用 scoped patch；越界或非法行号返回 None。"""
    if patch.start_line < edit_range.start_line:
        return None
    if patch.end_line > edit_range.end_line:
        return None
    if patch.start_line > patch.end_line:
        return None
    lines = text.split("\n")
    if patch.start_line < 1 or patch.end_line > len(lines):
        return None
    start = patch.start_line - 1
    end = patch.end_line
    return "\n".join([*lines[:start], *patch.replacement_lines, *lines[end:]])


def parse_repair_response(
    response: Any,
    context: CodeRepairContext,
) -> CodeRepairAttempt:
    """解析 LLM scoped repair JSON。"""
    if not response.choices:
        return CodeRepairAttempt(
            context=context, flags=["code.repair.empty_choices"],
        )
    choice = response.choices[0]
    raw = choice.message.content or ""
    if getattr(choice, "finish_reason", None) == "length":
        return CodeRepairAttempt(
            context=context, flags=["code.repair.truncated"],
        )
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError:
        return CodeRepairAttempt(
            context=context, flags=["code.repair.json_decode_error"],
        )
    patch = _parse_patch(data.get("patch"))
    unresolved = _parse_unresolved(data.get("unresolved"))
    flags: list[str] = []
    if patch is None:
        flags.append("code.repair.unresolved")
    elif (
        patch.start_line < context.edit_range.start_line
        or patch.end_line > context.edit_range.end_line
    ):
        flags.append("code.repair.reject_scope")
        patch = None
    return CodeRepairAttempt(
        context=context,
        patch=patch,
        plan=str(data.get("plan", "")),
        dependency_assessment=str(data.get("dependency_assessment", "")),
        unresolved=unresolved,
        flags=flags,
    )


def _merge_line_windows(
    failing_lines: list[int], line_count: int, radius: int,
) -> list[CodeEditRange]:
    ranges: list[CodeEditRange] = []
    for line in failing_lines:
        start = max(1, line - radius)
        end = min(line_count, line + radius)
        if ranges and start <= ranges[-1].end_line + 1:
            ranges[-1] = CodeEditRange(
                ranges[-1].start_line, max(ranges[-1].end_line, end),
            )
        else:
            ranges.append(CodeEditRange(start, end))
    return ranges


def _numbered_lines(lines: list[str], edit_range: CodeEditRange) -> list[str]:
    return [
        f"{line_no}: {lines[line_no - 1]}"
        for line_no in range(edit_range.start_line, edit_range.end_line + 1)
    ]


def _file_outline(lines: list[str]) -> list[str]:
    outline: list[str] = []
    for index, line in enumerate(lines, start=1):
        if _SYMBOL_RE.match(line):
            outline.append(f"{index}: {line.strip()[:160]}")
    return outline[:80]


def _enclosing_symbols(lines: list[str], line_no: int) -> list[str]:
    symbols: list[str] = []
    for index in range(min(line_no - 1, len(lines) - 1), -1, -1):
        line = lines[index]
        if _SYMBOL_RE.match(line):
            symbols.append(f"{index + 1}: {line.strip()[:160]}")
        if len(symbols) >= 3:
            break
    return list(reversed(symbols))


def _path_candidates(source: SourceFile) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for page in source.pages:
        for candidate in page.meta.path_candidates:
            candidates.append(asdict(candidate))
    return candidates[:12]


def _related_snippets(
    source: SourceFile,
    related_sources: list[SourceFile],
) -> list[str]:
    snippets: list[str] = []
    source_dir = source.path.rsplit("/", 1)[0] if "/" in source.path else ""
    for related in related_sources:
        if related.path == source.path:
            continue
        related_dir = related.path.rsplit("/", 1)[0] if "/" in related.path else ""
        if source_dir and related_dir != source_dir:
            continue
        first_lines = "\n".join(related.merged_text.split("\n")[:20])
        snippets.append(f"file: {related.path}\n{first_lines}")
        if len(snippets) >= 3:
            break
    return snippets


def _parse_patch(raw: object) -> ScopedPatch | None:
    if not isinstance(raw, dict):
        return None
    try:
        replacement = raw.get("replacement_lines")
        if not isinstance(replacement, list):
            return None
        return ScopedPatch(
            start_line=int(raw.get("start_line", 0)),
            end_line=int(raw.get("end_line", 0)),
            replacement_lines=[str(line) for line in replacement],
        )
    except (TypeError, ValueError):
        return None


def _parse_unresolved(raw: object) -> list[CodeUnresolved]:
    if not isinstance(raw, list):
        return []
    out: list[CodeUnresolved] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(CodeUnresolved(
                line=int(item.get("line", 0)),
                context=str(item.get("context", "")),
                note=str(item.get("note", "")),
            ))
        except (TypeError, ValueError):
            continue
    return out


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _diagnostic_score(diagnostics: list[CodeDiagnostic]) -> int:
    score = 0
    for diagnostic in diagnostics:
        if diagnostic.status == "syntax_dirty":
            score += 100 + diagnostic.syntax_errors + len(diagnostic.failing_lines)
        elif diagnostic.status == "failed":
            score += 50
        elif diagnostic.status == "semantic_dirty":
            score += 5
    return score


def _with_flag(attempt: CodeRepairAttempt, flag: str) -> CodeRepairAttempt:
    return CodeRepairAttempt(
        context=attempt.context,
        patch=attempt.patch,
        plan=attempt.plan,
        dependency_assessment=attempt.dependency_assessment,
        unresolved=attempt.unresolved,
        flags=[*attempt.flags, flag],
    )


def _collect_flags(attempts: list[CodeRepairAttempt]) -> list[str]:
    out: list[str] = []
    for attempt in attempts:
        out.extend(attempt.flags)
    return out


def _collect_unresolved(
    attempts: list[CodeRepairAttempt],
) -> list[CodeUnresolved]:
    out: list[CodeUnresolved] = []
    for attempt in attempts:
        out.extend(attempt.unresolved)
    return out
