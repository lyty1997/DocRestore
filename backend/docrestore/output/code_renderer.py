# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""代码源文件渲染器（AGE-8 Phase 2.4）

把 ``code_file_grouping.SourceFile`` 写到磁盘：
  - 每个 SourceFile → ``output_dir/<files_dir>/<relative-path>``
  - 索引：``output_dir/files-index.json``（含 path / language / source_pages /
    line_count / line_no_range / quality flags）
  - 兼容旧 UI：``output_dir/document.md``（每文件 H2 标题 + 围栏代码块）

**安全**：路径穿越防护——拒绝 ``..`` / 绝对路径，统一 rel 到 ``files/``。
路径含非法字符 → 替换为 ``_unknown/`` 兜底，不抛异常打断 batch。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import PageColumn, SourceFile
    from docrestore.processing.code_diagnostics import (
        CodeDiagnostic,
        CodeDiagnosticRunner,
    )

logger = logging.getLogger(__name__)

#: 路径穿越/非法字符兜底目录
_UNKNOWN_DIR = "_unknown"
_INDEX_FILENAME = "files-index.json"
_COMPAT_DOCUMENT_FILENAME = "document.md"


@dataclass
class CodeRenderResult:
    """渲染结果"""

    files_dir: Path                 # output_dir/<files_dir>/
    index_path: Path                # files-index.json
    document_path: Path             # 兼容 document.md
    written_files: list[Path]       # 实际写出的源文件路径
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # ↑ (canonical_path, reason) 路径被拒/降级的记录
    diagnostics: list[CodeDiagnostic] = field(default_factory=list)
    # ↑ AGE-65 多语言语法诊断结果


async def render_code_files(
    sources: list[SourceFile],
    output_dir: Path,
    *,
    files_subdir: str = "files",
    enable_diagnostics: bool = False,
    diagnostic_runner: CodeDiagnosticRunner | None = None,
) -> CodeRenderResult:
    """把 list[SourceFile] 写出到 output_dir，附带索引与兼容 markdown。

    Args:
        sources: ``code_file_grouping.group_into_files()`` 的输出
        output_dir: 任务输出根目录（``output/<task>/``）
        files_subdir: 源文件子目录名（默认 ``files``）
        enable_diagnostics: 是否运行 AGE-65 多语言轻量诊断
        diagnostic_runner: 测试/定制时注入的诊断运行器

    Returns:
        CodeRenderResult：files_dir / index_path / document_path 等。
    """
    files_dir = output_dir / files_subdir
    files_dir.mkdir(parents=True, exist_ok=True)
    from docrestore.processing.code_file_grouping import segment_from_page_column
    from docrestore.processing.code_diagnostics import (
        CodeDiagnosticRunner,
        CodeDiagnosticTarget,
    )

    written: list[Path] = []
    skipped: list[tuple[str, str]] = []
    safe_sources: list[tuple[SourceFile, str, Path]] = []
    index_entries: list[dict[str, object]] = []
    document_chunks: list[str] = []

    for src in sources:
        rel_path, reason = _safe_relative_path(src.path)
        if reason:
            skipped.append((src.path, reason))
        target = files_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(target, "w", encoding="utf-8") as f:
            await f.write(src.merged_text)
            if src.merged_text and not src.merged_text.endswith("\n"):
                await f.write("\n")
        written.append(target)
        safe_sources.append((src, rel_path, target))

    diagnostics: list[CodeDiagnostic] = []
    if enable_diagnostics and safe_sources:
        runner = diagnostic_runner or CodeDiagnosticRunner()
        targets = [
            CodeDiagnosticTarget(
                path=rel_path,
                file_path=target,
                language=src.language,
                include_root=files_dir,
            )
            for src, rel_path, target in safe_sources
        ]
        diagnostics = await asyncio.to_thread(runner.run_targets, targets)
    diagnostics_by_path = {item.path: item for item in diagnostics}

    for src, rel_path, _target in safe_sources:
        flags = _collect_code_flags(src)
        diagnostic = diagnostics_by_path.get(rel_path)
        entry: dict[str, object] = {
            "path": rel_path,
            "filename": src.filename,
            "language": src.language,
            "source_pages": [
                f"{p.page_stem}.col{p.column_index}" for p in src.pages
            ],
            "source_page_ranges": [
                {
                    "page": f"{p.page_stem}.col{p.column_index}",
                    "start_line": _column_line_no_start(p),
                    "end_line": _column_line_no_end(p),
                }
                for p in src.pages
            ],
            "source_page_count": len(src.pages),
            "source_column_count": len({
                f"{p.page_stem}.col{p.column_index}" for p in src.pages
            }),
            "line_count": src.line_count,
            "line_no_range": list(src.line_no_range),
            "flags": flags,
            "source_file_flags": list(src.flags),
            "source_column_flags": [
                {
                    "page": f"{p.page_stem}.col{p.column_index}",
                    "meta_flags": list(p.meta.flags),
                    "column_flags": list(p.column.flags),
                }
                for p in src.pages
                if p.meta.flags or p.column.flags
            ],
            "path_confidence": _source_path_confidence(src),
            "source_segments": [
                asdict(segment_from_page_column(p)) for p in src.pages
            ],
            "quality": _quality_summary(flags, diagnostic),
        }
        if diagnostic is not None:
            entry.update(_diagnostic_index_fields(diagnostic))
        index_entries.append(entry)

        document_chunks.append(_render_document_chunk(rel_path, src))

    index_path = output_dir / _INDEX_FILENAME
    async with aiofiles.open(index_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(index_entries, ensure_ascii=False, indent=2))

    document_path = output_dir / _COMPAT_DOCUMENT_FILENAME
    async with aiofiles.open(document_path, "w", encoding="utf-8") as f:
        await f.write("\n\n".join(document_chunks) + "\n")

    return CodeRenderResult(
        files_dir=files_dir,
        index_path=index_path,
        document_path=document_path,
        written_files=written,
        skipped=skipped,
        diagnostics=diagnostics,
    )


def _safe_relative_path(raw_path: str) -> tuple[str, str | None]:
    """把 SourceFile.path 规范化为安全的相对路径。

    返回 ``(safe_relative_path, reason)``：
      - reason=None：路径合法，原样使用
      - reason="absolute" / "traversal" / "empty"：触发降级，路径放到
        ``_unknown/<sanitized>``
    """
    if not raw_path or not raw_path.strip():
        return f"{_UNKNOWN_DIR}/_empty", "empty"

    p = Path(raw_path)
    if p.is_absolute():
        return f"{_UNKNOWN_DIR}/{p.name}", "absolute"

    # 拒绝任何 .. 段（即使在中间）
    parts = list(p.parts)
    if any(seg == ".." for seg in parts):
        return f"{_UNKNOWN_DIR}/{p.name}", "traversal"

    # 过滤空段、当前目录段
    cleaned = [seg for seg in parts if seg and seg != "."]
    if not cleaned:
        return f"{_UNKNOWN_DIR}/_empty", "empty"

    return "/".join(cleaned), None


def _render_document_chunk(rel_path: str, src: SourceFile) -> str:
    """单个 SourceFile 的 markdown 块（H2 + 围栏代码）"""
    lang = src.language or ""
    flags = _collect_code_flags(src)
    flag_line = (
        f"<!-- flags: {', '.join(flags)} -->\n" if flags else ""
    )
    pages_line = (
        "<!-- source_pages: "
        f"{', '.join(p.page_stem + '.col' + str(p.column_index) for p in src.pages)}"
        " -->\n"
    )
    return (
        f"## `{rel_path}`\n\n"
        f"{pages_line}"
        f"{flag_line}"
        f"```{lang}\n"
        f"{src.merged_text}\n"
        f"```"
    )


def _collect_code_flags(src: SourceFile) -> list[str]:
    """收集 SourceFile 及其来源 column 的代码质量 flags。"""
    flags: list[str] = []
    seen: set[str] = set()

    def add(flag: str) -> None:
        if flag and flag not in seen:
            seen.add(flag)
            flags.append(flag)

    for flag in src.flags:
        add(flag)
    for page in src.pages:
        for flag in page.meta.flags:
            add(flag)
        for flag in page.column.flags:
            add(flag)
    return flags


def _quality_summary(
    flags: list[str],
    diagnostic: CodeDiagnostic | None = None,
) -> dict[str, object]:
    """给 files-index.json 提供向后兼容的轻量质量摘要。"""
    risk_codes = [_flag_to_risk_code(flag) for flag in flags]
    risk_codes = [code for code in risk_codes if code]
    if diagnostic is not None:
        risk = _diagnostic_risk_code(diagnostic)
        if risk:
            risk_codes.append(risk)
    severity = "ok"
    if any(code in _WARN_RISK_CODES for code in risk_codes):
        severity = "warn"
    elif risk_codes:
        severity = "info"
    return {
        "severity": severity,
        "risk_codes": risk_codes,
        "flag_count": len(flags),
    }


_WARN_RISK_CODES: frozenset[str] = frozenset({
    "code.refine.truncated",
    "code.grouping.large_gap_collapsed",
    "code.grouping.no_filename",
    "code.grouping.path_safety",
    "code.assembly.no_char_width",
    "code.assembly.no_line_height",
    "code.diagnostic.syntax_dirty",
    "code.diagnostic.failed",
    "code.repair.truncated",
    "code.repair.reject_diagnostic_worse",
    "code.audit.reject_diagnostic_worse",
    "code.audit.reject_readonly_patch",
})


def _flag_to_risk_code(flag: str) -> str:
    """把内部 flag 归一为稳定风险 code；未知 flag 原样保留。"""
    if flag.startswith("code.grouping.missing_line_nos="):
        return "code.grouping.missing_line_nos"
    if flag.startswith("code.line_gap_count="):
        return "code.assembly.line_gap_count"
    if flag.startswith("code.assembly.unpaired_codes="):
        return "code.assembly.unpaired_codes"
    if flag.startswith("code.repair.truncated"):
        return "code.repair.truncated"
    if flag.startswith("code.repair.unresolved"):
        return "code.repair.unresolved"
    if flag.startswith("code.repair.reject_diagnostic_worse"):
        return "code.repair.reject_diagnostic_worse"
    if flag.startswith("code.audit.reject_diagnostic_worse"):
        return "code.audit.reject_diagnostic_worse"
    if flag.startswith("code.audit.reject_readonly_patch"):
        return "code.audit.reject_readonly_patch"
    if flag.startswith("code.grouping.merged_pages="):
        return ""
    return flag


def _diagnostic_index_fields(
    diagnostic: CodeDiagnostic,
) -> dict[str, object]:
    """生成新 diagnostic 字段和旧 compile_* 兼容字段。"""
    fields: dict[str, object] = {
        "diagnostic": diagnostic.to_index_dict(),
        "compile_status": _legacy_compile_status(diagnostic.status),
        "compile_failing_lines": list(diagnostic.failing_lines),
        "compile_syntax_errors": diagnostic.syntax_errors,
        "compile_semantic_errors": diagnostic.semantic_errors,
    }
    if diagnostic.summary:
        fields["compile_error"] = diagnostic.summary[:1000]
    if diagnostic.status in {"tool_unavailable", "unsupported"}:
        fields["compile_skip_reason"] = diagnostic.summary
    return fields


def _legacy_compile_status(status: str) -> str:
    """把 AGE-65 诊断状态映射到旧前端可识别 compile_status。"""
    if status == "syntax_clean":
        return "passed"
    if status in {"tool_unavailable", "unsupported"}:
        return "skipped"
    return "failed"


def _diagnostic_risk_code(diagnostic: CodeDiagnostic) -> str:
    """把诊断状态映射为质量摘要 code。"""
    if diagnostic.status == "syntax_dirty":
        return "code.diagnostic.syntax_dirty"
    if diagnostic.status == "semantic_dirty":
        return "code.diagnostic.semantic_dirty"
    if diagnostic.status == "dependency_dirty":
        return "code.diagnostic.dependency_dirty"
    if diagnostic.status == "tool_unavailable":
        return "code.diagnostic.tool_unavailable"
    if diagnostic.status == "failed":
        return "code.diagnostic.failed"
    return ""


def _source_path_confidence(src: SourceFile) -> float:
    """SourceFile 的保守路径置信度：取来源 column 的最低非零值。"""
    values = [
        page.meta.path_confidence
        for page in src.pages
        if page.meta.path_confidence > 0
    ]
    if not values:
        return 0.0
    return round(min(values), 3)


def _column_line_no_start(page: PageColumn) -> int:
    """返回来源页代码列的最小行号。"""
    if not page.column.lines:
        return 0
    return min(line.line_no for line in page.column.lines)


def _column_line_no_end(page: PageColumn) -> int:
    """返回来源页代码列的最大行号。"""
    if not page.column.lines:
        return 0
    return max(line.line_no for line in page.column.lines)
