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

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import SourceFile

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


async def render_code_files(
    sources: list[SourceFile],
    output_dir: Path,
    *,
    files_subdir: str = "files",
) -> CodeRenderResult:
    """把 list[SourceFile] 写出到 output_dir，附带索引与兼容 markdown。

    Args:
        sources: ``code_file_grouping.group_into_files()`` 的输出
        output_dir: 任务输出根目录（``output/<task>/``）
        files_subdir: 源文件子目录名（默认 ``files``）

    Returns:
        CodeRenderResult：files_dir / index_path / document_path 等。
    """
    files_dir = output_dir / files_subdir
    files_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped: list[tuple[str, str]] = []
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

        index_entries.append({
            "path": rel_path,
            "filename": src.filename,
            "language": src.language,
            "source_pages": [
                f"{p.page_stem}.col{p.column_index}" for p in src.pages
            ],
            "line_count": src.line_count,
            "line_no_range": list(src.line_no_range),
            "flags": src.flags,
        })

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
    flag_line = (
        f"<!-- flags: {', '.join(src.flags)} -->\n" if src.flags else ""
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
