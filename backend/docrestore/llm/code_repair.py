# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""诊断驱动的代码 scoped repair。

该模块实现“编辑范围小、只读上下文大”的 LLM 修复基础能力。LLM 只能返回
落在 edit_range 内的 patch；同文件 outline、来源页、路径候选和相关片段
只作为 prompt 上下文，不允许被 patch 修改。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from docrestore.llm.base import BaseLLMRefiner
from docrestore.llm.json_extract import extract_json
from docrestore.llm.code_refine import (
    CodeRefineResult,
    CodeUnresolved,
    RedactText,
)
from docrestore.llm.prompts import (
    build_code_consistency_audit_prompt,
    build_code_repair_prompt,
)
from docrestore.processing.code_diagnostics import (
    CodeDiagnostic,
    CodeDiagnosticRunner,
    diagnose_text,
)

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import SourceFile
    from docrestore.processing.code_context import CodeContextProvider

logger = logging.getLogger(__name__)

_SYMBOL_RE = re.compile(
    r"^\s*(?:class|def|function|func|struct|enum|interface|type|impl|"
    r"namespace)\s+([A-Za-z_][A-Za-z0-9_]*)"
)

#: repair 窗口循环硬上限（#94）。病态 OCR 大文件会产生大量 syntax_dirty 失败行 →
#: 大量窗口，每窗口一次 ~30s LLM 调用 + 一次 g++ 重诊断，串行不收敛会把任务卡死。
#: 超限只处理前 N 个窗口（已按 start_line 升序，保持 line_offset 重映射不变量，
#: 不按严重度重排——重排会破坏 _shift_range 偏移模型），其余窗口范围作为 unresolved
#: 透出，绝不静默丢弃。
_MAX_REPAIR_WINDOWS = 12
#: 连续「无改善」窗口早停阈值（#94）。无改善 = 未落 patch（无 patch / reject_scope /
#: reject_truncation / reject_diagnostic_worse 任一）。从第 1 个窗口起计、每落一个
#: patch 清零；病态文件（LLM 反复给不出可用 patch）约 3 个窗口（~90s）即放弃、回退
#: 已修部分。从 1 起计而非「首个 patch 之后」是关键：#94 零落 patch 文件后者永不触发。
_MAX_REPAIR_NO_IMPROVEMENT = 3
#: 正文超此字符数视为超大病态文件，直接跳过 scoped repair（连 N 窗口都不值得发），
#: 回退原文 + warning + flag，失败行作为 unresolved 透出。比 pipeline 层 clean 大文件
#: 行阈值（_CODE_REPAIR_LARGE_FILE_LINE_THRESHOLD=400）宽，只拦真正的巨型文件。
_MAX_REPAIR_CHARS = 60_000
#: audit patch 应用循环防御上限。audit 仅一次 LLM 调用（max_tokens=4096，patch 数天然
#: 受限），但每个 patch 仍跑一次 g++ 重诊断；防御性封顶避免极端响应拖慢，非 #94 卡死源。
_MAX_AUDIT_PATCHES = 24


def is_oversized_for_repair(text: str) -> bool:
    """正文是否超过 scoped repair 字符上限（#94 熔断闸口，repair 与 audit 共用）。

    repair 与 audit 用同一闸口：超大文件 repair 熔断回退原文后，audit 也必须跳过，
    否则会在「本应回退原文」的巨型文件上仍发 1 次 LLM + 跑全量 g++，甚至改写它，
    与熔断承诺不一致（#94 review 缺口）。
    """
    return len(text) > _MAX_REPAIR_CHARS


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


@dataclass(frozen=True)
class CandidateRange:
    """审计发现但未授权修改的候选范围。"""

    start_line: int
    end_line: int
    reason: str = ""


@dataclass(frozen=True)
class AuditPatch:
    """全文件一致性审计返回的 patch。"""

    patch: ScopedPatch
    evidence: str = ""


@dataclass(frozen=True)
class CodeConsistencyAuditContext:
    """全文件一致性审计 prompt 上下文。"""

    file_path: str
    language: str | None
    editable_ranges: list[CodeEditRange]
    read_only_excerpts: list[str]
    file_outline: list[str]
    symbol_table: list[str]
    diagnostics: list[dict[str, object]]
    previous_repairs: list[str]
    repeated_ocr_confusions: list[dict[str, object]]
    unresolved_items: list[dict[str, object]]
    related_snippets: list[str]
    constraints: list[str]

    def to_prompt_json(self) -> str:
        """序列化为 prompt JSON。"""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class CodeConsistencyAuditAttempt:
    """全文件一致性审计 LLM 响应。"""

    context: CodeConsistencyAuditContext
    patches: list[AuditPatch] = field(default_factory=list)
    plan: str = ""
    candidate_ranges: list[CandidateRange] = field(default_factory=list)
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
        redact: RedactText | None = None,
    ) -> None:
        self._base = base
        self._diagnostic_runner = diagnostic_runner
        self._window_radius = window_radius
        # #36：file_path / related_snippets / path_candidates / diagnostics 拼进
        # 云端 prompt 前的脱敏函数；None = 未开 PII，不脱。
        self._redact = redact

    async def repair(
        self,
        source: SourceFile,
        diagnostics: list[CodeDiagnostic],
        *,
        related_sources: list[SourceFile] | None = None,
        context_provider: CodeContextProvider | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> CodeRefineResult:
        """按诊断窗口修复 SourceFile；失败或恶化时回退原文。

        三重收敛兜底（#94）防止病态 OCR 大文件把任务卡死：
        1. 超大文件熔断：正文超 ``_MAX_REPAIR_CHARS`` 直接跳过、回退原文。
        2. 窗口预算上限 ``_MAX_REPAIR_WINDOWS``：超限只处理前 N 个窗口（保持升序）。
        3. 连续无改善早停 ``_MAX_REPAIR_NO_IMPROVEMENT``：LLM 反复给不出可用 patch
           即放弃、回退已修部分。
        被跳过/早停的窗口范围一律收进 unresolved 透出（不埋雷，让缺修复可见）。
        ``progress_cb(window_index, window_total)`` 每窗口上报，避免前端长时间静止。
        """
        # 超大病态文件熔断：连 N 窗口都不值得发，直接回退原文 + warning，失败行透出。
        if is_oversized_for_repair(source.merged_text):
            logger.warning(
                "CodeRepair 跳过超大文件 (path=%s, chars=%d > %d)，回退原文",
                source.path, len(source.merged_text), _MAX_REPAIR_CHARS,
            )
            return CodeRefineResult(
                refined_text=source.merged_text,
                unresolved=_failing_line_unresolved(
                    diagnostics, "code.repair.skipped_oversized",
                ),
                flags=[
                    f"code.repair.skipped_oversized={len(source.merged_text)}",
                ],
            )

        # build_repair_contexts 内含参考源码树的 rglob/read_text 等阻塞 IO，
        # 放到线程里跑，避免阻塞事件循环（B7 C12）。
        contexts = await asyncio.to_thread(
            build_repair_contexts,
            source,
            diagnostics,
            related_sources=related_sources or [],
            context_provider=context_provider,
            window_radius=self._window_radius,
            redact=self._redact,
        )
        if not contexts:
            return CodeRefineResult(
                refined_text=source.merged_text,
                flags=["code.repair.no_windows"],
            )

        # 窗口预算：超限只处理前 N 个（已按 start_line 升序，保持 line_offset 不变量）；
        # 被丢弃窗口的范围收进 budget_unresolved 透出，绝不静默吞（#94 不埋雷）。
        total_windows = len(contexts)
        windows = contexts[:_MAX_REPAIR_WINDOWS]
        budget_flags: list[str] = []
        budget_unresolved: list[CodeUnresolved] = list(
            _skipped_window_unresolved(contexts[_MAX_REPAIR_WINDOWS:]),
        )
        if total_windows > _MAX_REPAIR_WINDOWS:
            budget_flags.append(
                f"code.repair.window_cap={total_windows - _MAX_REPAIR_WINDOWS}",
            )

        original = source.merged_text
        current = original
        attempts: list[CodeRepairAttempt] = []
        original_score = _diagnostic_score(diagnostics)
        # 隔离诊断需共置兄弟文件让同目录 #include 可解析，否则缺失头会被判
        # dependency_dirty(score 0)、骗过下方"诊断未恶化"接受门（自审 followup）。
        siblings = _sibling_files(source, related_sources)
        # 已应用 patch 造成的累计行偏移。窗口之间互不重叠（_merge_line_windows
        # 保证有间隔），故前一个 patch 只平移后续窗口的行号、不改其文本内容；
        # patch/edit_range 都是原文行号，按偏移平移到 current 坐标系再应用（B7 C2）。
        line_offset = 0
        # 连续无改善计数（落 patch 清零），到阈值早停；记录早停位置以透出剩余窗口。
        no_improvement = 0
        stopped_at: int | None = None
        for index, context in enumerate(windows):
            if progress_cb is not None:
                progress_cb(index + 1, len(windows))
            attempt = await self._repair_one_window(current, source, context)
            attempts.append(attempt)
            applied = await self._try_apply_window(
                attempt, context, current, line_offset, source, siblings,
                original_score, attempts,
            )
            if applied is not None:
                current, line_offset = applied
                no_improvement = 0
            else:
                no_improvement += 1
                if no_improvement >= _MAX_REPAIR_NO_IMPROVEMENT:
                    stopped_at = index
                    break

        if stopped_at is not None:
            remaining = windows[stopped_at + 1:]
            budget_flags.append(f"code.repair.early_stop={stopped_at + 1}")
            budget_unresolved.extend(_skipped_window_unresolved(remaining))

        if current == original:
            return CodeRefineResult(
                refined_text=original,
                unresolved=[*_collect_unresolved(attempts), *budget_unresolved],
                flags=[
                    *_collect_flags(attempts), *budget_flags,
                ] or ["code.repair.no_change"],
            )

        return CodeRefineResult(
            refined_text=current,
            unresolved=[*_collect_unresolved(attempts), *budget_unresolved],
            flags=[
                f"code.repair.windows={total_windows}",
                f"code.repair.applied={sum(1 for a in attempts if a.patch)}",
                *_collect_flags(attempts),
                *budget_flags,
            ],
        )

    async def _try_apply_window(
        self,
        attempt: CodeRepairAttempt,
        context: CodeRepairContext,
        current: str,
        line_offset: int,
        source: SourceFile,
        siblings: list[tuple[str, str]],
        original_score: int,
        attempts: list[CodeRepairAttempt],
    ) -> tuple[str, int] | None:
        """尝试落一个窗口的 patch；接受返回 ``(new_text, new_offset)``，否则 None。

        拒绝时就地把对应 reject flag 写回 ``attempts[-1]``（与早停/无改善计数一致：
        任何未落 patch 都算一次无改善）。
        """
        if attempt.patch is None or any(
            flag.startswith("code.repair.reject") for flag in attempt.flags
        ):
            return None
        if _is_truncating_patch(attempt.patch):
            attempts[-1] = _with_flag(attempt, "code.repair.reject_truncation")
            return None
        patched = apply_scoped_patch(
            current,
            _shift_range(context.edit_range, line_offset),
            _shift_patch(attempt.patch, line_offset),
        )
        if patched is None:
            attempts[-1] = _with_flag(attempt, "code.repair.reject_scope")
            return None
        post = await asyncio.to_thread(
            diagnose_text,
            path=source.path,
            language=source.language,
            text=patched,
            sibling_files=siblings,
            runner=self._diagnostic_runner,
        )
        if _diagnostic_score([post]) > original_score:
            attempts[-1] = _with_flag(
                attempt, "code.repair.reject_diagnostic_worse",
            )
            return None
        return patched, line_offset + _patch_line_delta(attempt.patch)

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


class CodeConsistencyAuditor:
    """小窗口修复后的全文件一致性审计 pass。"""

    def __init__(
        self,
        base: BaseLLMRefiner,
        *,
        diagnostic_runner: CodeDiagnosticRunner | None = None,
        excerpt_radius: int = 4,
        redact: RedactText | None = None,
    ) -> None:
        self._base = base
        self._diagnostic_runner = diagnostic_runner
        self._excerpt_radius = excerpt_radius
        # #36：审计 prompt 同样脱敏 file_path / related_snippets / diagnostics
        self._redact = redact

    async def audit(
        self,
        source: SourceFile,
        diagnostics: list[CodeDiagnostic],
        *,
        previous_result: CodeRefineResult,
        related_sources: list[SourceFile] | None = None,
        context_provider: CodeContextProvider | None = None,
    ) -> CodeRefineResult:
        """审计全文件一致性，只应用授权范围内的 scoped patches。"""
        # 超大文件与 repair 用同一熔断闸口：repair 已回退原文，audit 若不一并跳过会在
        # 该巨型文件上仍发 1 次 LLM + 跑全量 g++、甚至改写它（#94 review 缺口）。
        if is_oversized_for_repair(source.merged_text):
            logger.warning(
                "CodeConsistencyAudit 跳过超大文件 (path=%s, chars=%d > %d)",
                source.path, len(source.merged_text), _MAX_REPAIR_CHARS,
            )
            return CodeRefineResult(
                refined_text=source.merged_text,
                unresolved=list(previous_result.unresolved),
                flags=["code.audit.skipped_oversized"],
            )
        # build_consistency_audit_context 同样含参考源码树阻塞 IO，放到线程里。
        context = await asyncio.to_thread(
            build_consistency_audit_context,
            source,
            diagnostics,
            previous_result=previous_result,
            related_sources=related_sources or [],
            context_provider=context_provider,
            excerpt_radius=self._excerpt_radius,
            redact=self._redact,
        )
        if not context.editable_ranges:
            return CodeRefineResult(
                refined_text=source.merged_text,
                unresolved=list(previous_result.unresolved),
                flags=["code.audit.no_editable_ranges"],
            )
        attempt = await self._run_audit(context)
        if attempt.patches == []:
            return CodeRefineResult(
                refined_text=source.merged_text,
                unresolved=[*previous_result.unresolved, *attempt.unresolved],
                flags=attempt.flags or [
                    f"code.audit.candidate_ranges={len(attempt.candidate_ranges)}"
                ],
            )

        original = source.merged_text
        current = original
        applied = 0
        baseline_score = _diagnostic_score(diagnostics)
        siblings = _sibling_files(source, related_sources)
        # 多个 audit patch 顺序应用同样会移动后续 patch 的行号；按 start_line 升序
        # 处理并按累计偏移平移到 current 坐标系（B7 C2/C3）。
        line_offset = 0
        # 防御性封顶：每个 patch 都跑一次 g++ 重诊断，极端响应下封顶处理数（#94）；
        # 按 start_line 升序后取前 N 个（保持偏移模型），超限计 flag 透出。
        ordered_patches = sorted(
            attempt.patches, key=lambda ap: ap.patch.start_line,
        )
        if len(ordered_patches) > _MAX_AUDIT_PATCHES:
            attempt = _with_audit_flag(
                attempt,
                f"code.audit.patch_cap={len(ordered_patches) - _MAX_AUDIT_PATCHES}",
            )
            ordered_patches = ordered_patches[:_MAX_AUDIT_PATCHES]
        for audit_patch in ordered_patches:
            outcome = await self._try_apply_audit_patch(
                audit_patch, context.editable_ranges, current, line_offset,
                baseline_score, source, siblings,
            )
            if isinstance(outcome, str):
                attempt = _with_audit_flag(attempt, outcome)
            else:
                current, line_offset = outcome
                applied += 1

        flags = [
            f"code.audit.patches={applied}",
            f"code.audit.candidate_ranges={len(attempt.candidate_ranges)}",
            *attempt.flags,
        ]
        if current == original:
            return CodeRefineResult(
                refined_text=original,
                unresolved=[*previous_result.unresolved, *attempt.unresolved],
                flags=flags or ["code.audit.no_change"],
            )
        return CodeRefineResult(
            refined_text=current,
            unresolved=[*previous_result.unresolved, *attempt.unresolved],
            flags=flags,
        )

    async def _try_apply_audit_patch(
        self,
        audit_patch: AuditPatch,
        editable_ranges: list[CodeEditRange],
        current: str,
        line_offset: int,
        baseline_score: int,
        source: SourceFile,
        siblings: list[tuple[str, str]],
    ) -> tuple[str, int] | str:
        """尝试落一个 audit patch：成功返回新文本与偏移，否则返回拒绝 flag。

        返回 ``(new_text, new_offset)`` 或 ``code.audit.reject_*`` flag 字符串；
        越权/截断/越界/恶化四类拒绝从 ``audit`` 抽出（控制复杂度），语义不变。
        """
        edit_range = _range_authorizing_patch(audit_patch.patch, editable_ranges)
        if edit_range is None:
            return "code.audit.reject_readonly_patch"
        if _is_truncating_patch(audit_patch.patch):
            return "code.audit.reject_truncation"
        patched = apply_scoped_patch(
            current,
            _shift_range(edit_range, line_offset),
            _shift_patch(audit_patch.patch, line_offset),
        )
        if patched is None:
            return "code.audit.reject_scope"
        post = await asyncio.to_thread(
            diagnose_text,
            path=source.path,
            language=source.language,
            text=patched,
            sibling_files=siblings,
            runner=self._diagnostic_runner,
        )
        if _diagnostic_score([post]) > baseline_score:
            return "code.audit.reject_diagnostic_worse"
        return patched, line_offset + _patch_line_delta(audit_patch.patch)

    async def _run_audit(
        self,
        context: CodeConsistencyAuditContext,
    ) -> CodeConsistencyAuditAttempt:
        messages = build_code_consistency_audit_prompt(context.to_prompt_json())
        kwargs = self._base._build_kwargs(messages)
        kwargs["max_tokens"] = 4096
        try:
            response = await self._base._call_llm(kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CodeConsistencyAudit LLM 调用失败，跳过: %s", exc)
            return CodeConsistencyAuditAttempt(
                context=context,
                flags=[f"code.audit.llm_error={type(exc).__name__}"],
            )
        return parse_consistency_audit_response(response, context)


def build_repair_contexts(
    source: SourceFile,
    diagnostics: list[CodeDiagnostic],
    *,
    related_sources: list[SourceFile],
    context_provider: CodeContextProvider | None = None,
    window_radius: int = 8,
    redact: RedactText | None = None,
) -> list[CodeRepairContext]:
    """根据诊断失败行生成 scoped repair contexts。

    ``redact`` 非空（#36）时，对拼进云端 prompt 的 file_path / related_snippets /
    path_candidates / diagnostics 在 ``json.dumps`` **之前**按字段脱敏——先脱再
    序列化，占位符里若含引号也会被 json 正确转义，绝不破坏 JSON。其余字段
    （local_lines / outline / symbols）派生自 ``source.merged_text``，已在更上游
    被 _redact_code_pii 脱过，无需重复。
    """
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
    file_path = source.path
    path_candidates = _path_candidates(source)
    related = _related_snippets(source, related_sources, context_provider)
    diag_dicts = [diagnostic.to_index_dict() for diagnostic in diagnostics]
    source_pages = [
        f"{page.page_stem}.col{page.column_index}" for page in source.pages
    ]
    if redact is not None:
        file_path = redact(file_path)
        path_candidates = [
            _redact_path_candidate(pc, redact) for pc in path_candidates
        ]
        related = [redact(snippet) for snippet in related]
        diag_dicts = [_redact_diag_dict(d, redact) for d in diag_dicts]
    return [
        CodeRepairContext(
            file_path=file_path,
            language=source.language,
            edit_range=edit_range,
            local_lines=_numbered_lines(lines, edit_range),
            enclosing_symbols=_enclosing_symbols(lines, edit_range.start_line),
            file_outline=outline,
            diagnostics=diag_dicts,
            related_snippets=related,
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


def build_consistency_audit_context(
    source: SourceFile,
    diagnostics: list[CodeDiagnostic],
    *,
    previous_result: CodeRefineResult,
    related_sources: list[SourceFile],
    context_provider: CodeContextProvider | None = None,
    excerpt_radius: int = 4,
    redact: RedactText | None = None,
) -> CodeConsistencyAuditContext:
    """组织全文件一致性审计上下文。

    ``redact`` 非空（#36 + #67）：file_path / related_snippets / diagnostics /
    unresolved_items（context·note 自由文本）在 ``json.dumps`` 前按字段脱敏；
    其余字段派生自已脱敏的 ``source.merged_text``。
    """
    lines = source.merged_text.split("\n")
    editable_ranges = _audit_editable_ranges(
        source, diagnostics, previous_result, lines,
    )
    file_path = source.path
    related = _related_snippets(source, related_sources, context_provider)
    diag_dicts = [diagnostic.to_index_dict() for diagnostic in diagnostics]
    if redact is not None:
        file_path = redact(file_path)
        related = [redact(snippet) for snippet in related]
        diag_dicts = [_redact_diag_dict(d, redact) for d in diag_dicts]
    return CodeConsistencyAuditContext(
        file_path=file_path,
        language=source.language,
        editable_ranges=editable_ranges,
        read_only_excerpts=_read_only_excerpts(
            lines, editable_ranges, excerpt_radius,
        ),
        file_outline=_file_outline(lines),
        symbol_table=_symbol_table(lines),
        diagnostics=diag_dicts,
        previous_repairs=list(previous_result.flags),
        repeated_ocr_confusions=_find_repeated_ocr_confusions(lines),
        unresolved_items=[
            _redact_unresolved_item(item, redact)
            for item in previous_result.unresolved
        ],
        related_snippets=related,
        constraints=[
            "patches must stay inside editable_ranges",
            "read_only_excerpts are evidence only and cannot be modified",
            "return candidate_ranges for issues outside editable_ranges",
            "do not rewrite the full file",
        ],
    )


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


def _shift_range(edit_range: CodeEditRange, offset: int) -> CodeEditRange:
    """把原文坐标的授权窗口平移到已应用 patch 后的 current 坐标系。"""
    return CodeEditRange(
        edit_range.start_line + offset, edit_range.end_line + offset,
    )


def _shift_patch(patch: ScopedPatch, offset: int) -> ScopedPatch:
    """把原文坐标的 patch 行号平移到 current 坐标系（替换内容不变）。"""
    return ScopedPatch(
        start_line=patch.start_line + offset,
        end_line=patch.end_line + offset,
        replacement_lines=patch.replacement_lines,
    )


def _patch_line_delta(patch: ScopedPatch) -> int:
    """patch 应用后造成的行数变化（正=增行，负=减行）。"""
    return len(patch.replacement_lines) - (patch.end_line - patch.start_line + 1)


# scoped 修复允许行数变化（合并/拆分/补符号），但替换行数比被替换区间骤减且
# 丢失过半内容时，多半是 LLM 把窗口"概括/截断"丢掉真实代码，按截断拒绝（B7 C4）。
_TRUNCATION_MIN_REMOVED = 4


def _is_truncating_patch(patch: ScopedPatch) -> bool:
    """判断 patch 是否疑似截断/概括（丢失过半且删除行数可观）。"""
    span = patch.end_line - patch.start_line + 1
    removed = span - len(patch.replacement_lines)
    if removed < _TRUNCATION_MIN_REMOVED:
        return False
    return len(patch.replacement_lines) * 2 < span


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
        data = json.loads(extract_json(raw))
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


def parse_consistency_audit_response(
    response: Any,
    context: CodeConsistencyAuditContext,
) -> CodeConsistencyAuditAttempt:
    """解析全文件一致性审计 JSON。"""
    if not response.choices:
        return CodeConsistencyAuditAttempt(
            context=context, flags=["code.audit.empty_choices"],
        )
    choice = response.choices[0]
    raw = choice.message.content or ""
    if getattr(choice, "finish_reason", None) == "length":
        return CodeConsistencyAuditAttempt(
            context=context, flags=["code.audit.truncated"],
        )
    try:
        data = json.loads(extract_json(raw))
    except json.JSONDecodeError:
        return CodeConsistencyAuditAttempt(
            context=context, flags=["code.audit.json_decode_error"],
        )

    patches = _parse_audit_patches(data.get("patches"))
    candidate_ranges = _parse_candidate_ranges(data.get("candidate_ranges"))
    unresolved = _parse_unresolved(data.get("unresolved"))
    return CodeConsistencyAuditAttempt(
        context=context,
        patches=patches,
        plan=str(data.get("plan", "")),
        candidate_ranges=candidate_ranges,
        unresolved=unresolved,
        flags=[],
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


def _audit_editable_ranges(
    source: SourceFile,
    diagnostics: list[CodeDiagnostic],
    previous_result: CodeRefineResult,
    lines: list[str],
) -> list[CodeEditRange]:
    candidate_lines: set[int] = set()
    for diagnostic in diagnostics:
        if diagnostic.status == "syntax_dirty":
            candidate_lines.update(
                line for line in diagnostic.failing_lines if line > 0
            )
    candidate_lines.update(
        item.line for item in previous_result.unresolved if item.line > 0
    )
    for confusion in _find_repeated_ocr_confusions(lines):
        raw_lines = confusion.get("lines", [])
        if isinstance(raw_lines, list):
            candidate_lines.update(
                line for line in raw_lines if isinstance(line, int) and line > 0
            )
    line_count = max(1, source.line_count)
    return _merge_line_windows(sorted(candidate_lines), line_count, radius=1)


def _read_only_excerpts(
    lines: list[str],
    editable_ranges: list[CodeEditRange],
    radius: int,
) -> list[str]:
    if not editable_ranges:
        return []
    excerpts: list[str] = []
    editable_line_nos = {
        line_no
        for edit_range in editable_ranges
        for line_no in range(edit_range.start_line, edit_range.end_line + 1)
    }
    included: set[int] = set()
    for edit_range in editable_ranges:
        start = max(1, edit_range.start_line - radius)
        end = min(len(lines), edit_range.end_line + radius)
        for line_no in range(start, end + 1):
            if line_no in editable_line_nos or line_no in included:
                continue
            included.add(line_no)
            excerpts.append(f"{line_no}: {lines[line_no - 1]}")
    return excerpts[:120]


def _symbol_table(lines: list[str]) -> list[str]:
    symbols: list[str] = []
    token_re = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
    seen: set[str] = set()
    for line in lines:
        for token in token_re.findall(line):
            if token in seen:
                continue
            seen.add(token)
            symbols.append(token)
            if len(symbols) >= 200:
                return symbols
    return symbols


def _find_repeated_ocr_confusions(
    lines: list[str],
) -> list[dict[str, object]]:
    token_re = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
    by_key: dict[str, dict[str, set[int]]] = {}
    for line_no, line in enumerate(lines, start=1):
        for token in token_re.findall(line):
            key = _ocr_confusion_key(token)
            by_key.setdefault(key, {}).setdefault(token, set()).add(line_no)

    out: list[dict[str, object]] = []
    for key, variants in by_key.items():
        if len(variants) < 2:
            continue
        if key == "":
            continue
        all_lines = sorted({
            line_no for line_set in variants.values() for line_no in line_set
        })
        out.append({
            "key": key,
            "variants": sorted(variants),
            "lines": all_lines,
        })
        if len(out) >= 20:
            break
    return out


def _ocr_confusion_key(token: str) -> str:
    table = str.maketrans({
        "0": "o",
        "O": "o",
        "o": "o",
        "1": "l",
        "I": "l",
        "l": "l",
    })
    return token.translate(table).lower()


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
    context_provider: CodeContextProvider | None = None,
) -> list[str]:
    snippets: list[str] = []
    if context_provider is not None:
        for candidate in context_provider.search_snippets(
            source.merged_text,
            language=source.language,
            limit=3,
        ):
            snippets.append(
                f"reference: {candidate.path}:{candidate.start_line}-"
                f"{candidate.end_line}\n{candidate.text}"
            )
            if len(snippets) >= 3:
                return snippets

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


def _redact_path_candidate(
    candidate: dict[str, object], redact: RedactText,
) -> dict[str, object]:
    """path_candidates dict 的 path/filename 脱敏（#36，送云端 prompt 前）。

    candidate 来自 OCR 的 IDE 路径串，可能含用户名等 PII；返回新 dict，不改原对象。
    """
    out = dict(candidate)
    for key in ("path", "filename"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = redact(value)
    return out


def _redact_diag_dict(
    diagnostic: dict[str, object], redact: RedactText,
) -> dict[str, object]:
    """诊断 dict 的自由文本字段脱敏（#36，送云端 prompt 前）。

    repair 的诊断在 _redact_code_pii **之前**算（基于原文）：g++/clang 的
    ``summary``（``output[:1000]``）带 caret 时会回显含 PII 的源码行，
    ``items[].message`` 也可能引用源码 token，``path`` 同 file_path——一并脱。
    其余字段（枚举 / 计数 / 行号）无 PII。返回新 dict（含新 items 列表），不改原
    对象（原对象还要写本地 files-index.json，保留原文）。
    """
    out = dict(diagnostic)
    for key in ("path", "summary"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = redact(value)
    items = out.get("items")
    if isinstance(items, list):
        redacted_items: list[object] = []
        for item in items:
            if isinstance(item, dict):
                new_item = dict(item)
                message = new_item.get("message")
                if isinstance(message, str):
                    new_item["message"] = redact(message)
                redacted_items.append(new_item)
            else:
                redacted_items.append(item)
        out["items"] = redacted_items
    return out


def _redact_unresolved_item(
    item: CodeUnresolved, redact: RedactText | None,
) -> dict[str, object]:
    """unresolved 项转 dict 并对自由文本 ``context``/``note`` 脱敏（#67 字段级加固）。

    ``context``/``note`` 是 LLM 标注的不可识别字符处上下文（OCR 源码片段），可能含
    结构化 PII（手机/邮箱）或注释里的人名。出云闸口（``_call_llm``）只兜底实体替换、
    不跑结构化，故此处补结构化 + 实体（``redact`` 已是带 lexicon 的 ``redact_for_cloud``
    闭包）。``redact`` 为 None（未开 PII）→ 按原样转 dict。返回新 dict，不改原对象。
    """
    out = asdict(item)
    if redact is None:
        return out
    for key in ("context", "note"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = redact(value)
    return out


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


def _parse_audit_patches(
    raw: object,
) -> list[AuditPatch]:
    if not isinstance(raw, list):
        return []
    out: list[AuditPatch] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        patch = _parse_patch(item)
        if patch is None:
            continue
        out.append(AuditPatch(
            patch=patch,
            evidence=str(item.get("evidence", "")),
        ))
    return out


def _parse_candidate_ranges(raw: object) -> list[CandidateRange]:
    if not isinstance(raw, list):
        return []
    out: list[CandidateRange] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start_line = int(item.get("start_line", 0))
            end_line = int(item.get("end_line", 0))
        except (TypeError, ValueError):
            continue
        if start_line <= 0 or end_line < start_line:
            continue
        out.append(CandidateRange(
            start_line=start_line,
            end_line=end_line,
            reason=str(item.get("reason", "")),
        ))
    return out


def _range_authorizing_patch(
    patch: ScopedPatch,
    editable_ranges: list[CodeEditRange],
) -> CodeEditRange | None:
    for edit_range in editable_ranges:
        if (
            patch.start_line >= edit_range.start_line
            and patch.end_line <= edit_range.end_line
        ):
            return edit_range
    return None


def _sibling_files(
    source: SourceFile,
    related_sources: list[SourceFile] | None,
) -> list[tuple[str, str]]:
    """供隔离诊断共置的同组兄弟文件 ``(path, text)``，排除目标自身。

    让 post-patch ``diagnose_text`` 能解析目标的同目录 ``#include``，与基线
    （``diagnose_source_files`` 共置全部源）可比，避免缺失头骗过接受门。
    """
    return [
        (s.path, s.merged_text)
        for s in (related_sources or [])
        if s.path != source.path
    ]


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


def _with_audit_flag(
    attempt: CodeConsistencyAuditAttempt,
    flag: str,
) -> CodeConsistencyAuditAttempt:
    return CodeConsistencyAuditAttempt(
        context=attempt.context,
        patches=attempt.patches,
        plan=attempt.plan,
        candidate_ranges=attempt.candidate_ranges,
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


def _skipped_window_unresolved(
    contexts: list[CodeRepairContext],
) -> list[CodeUnresolved]:
    """被 repair 预算跳过 / 早停的窗口范围 → unresolved（#94 不埋雷：让缺修复可见）。"""
    return [
        CodeUnresolved(
            line=context.edit_range.start_line,
            context="",
            note=(
                f"code.repair.skipped: 第 {context.edit_range.start_line}-"
                f"{context.edit_range.end_line} 行因 repair 预算未修复，请人工核对"
            ),
        )
        for context in contexts
    ]


def _failing_line_unresolved(
    diagnostics: list[CodeDiagnostic], tag: str,
) -> list[CodeUnresolved]:
    """超大文件熔断时把全部 syntax_dirty 失败行 → unresolved（截前 50 条防刷屏）。"""
    failing = sorted({
        line
        for diagnostic in diagnostics
        if diagnostic.status == "syntax_dirty"
        for line in diagnostic.failing_lines
        if line > 0
    })
    return [
        CodeUnresolved(
            line=line,
            context="",
            note=f"{tag}: 文件过大跳过 repair，请人工核对此行",
        )
        for line in failing[:50]
    ]
