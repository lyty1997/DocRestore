# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""AGE-8 E2E 端到端验收测试

完整链路：lines.jsonl (PaddleOCR basic) → ide_layout → ide_meta_extract
→ code_assembly → code_file_grouping → code_renderer → compile_check

数据：``output/age8-probe-basic/<stem>/lines.jsonl``（已用 PaddleOCR-VL
basic pipeline 跑过的 8 张 spike）。

如果 spike 数据不存在则 skip（CI 无 GPU/无 spike 数据时合理跳过）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from docrestore.models import TextLine
from docrestore.output.code_renderer import render_code_files
from docrestore.processing.code_assembly import assemble_columns
from docrestore.processing.code_file_grouping import (
    PageColumn,
    group_into_files,
)
from docrestore.processing.ide_layout import analyze_layout
from docrestore.processing.ide_meta_extract import extract_ide_metas

_SPIKE_LINES_DIR = (
    Path(__file__).resolve().parents[1].parent
    / "output" / "age8-probe-basic"
)
_SPIKE_IMAGE_DIR = (
    Path(__file__).resolve().parents[1].parent
    / "test_images" / "age8-spike"
)

# Phase 2 验收禁止字符（IDE UI 残留检测）
_FORBIDDEN_UI_TOKENS = ("PROBLEMS", "田田田田", "TIMELINE", "EXPLORER")


def _load_page_columns(stem: str) -> list[PageColumn]:
    from PIL import Image

    p = _SPIKE_LINES_DIR / stem / "lines.jsonl"
    items = [
        json.loads(line)
        for line in p.read_text().splitlines() if line.strip()
    ]
    text_lines = [
        TextLine(
            bbox=tuple(int(v) for v in it["bbox"][:4]),  # type: ignore[arg-type]
            text=it["text"],
            score=float(it.get("score", 1.0)),
        )
        for it in items
    ]
    img = _SPIKE_IMAGE_DIR / f"{stem}.JPG"
    sz = Image.open(img).size if img.exists() else (3400, 1900)
    layout = analyze_layout(text_lines, sz)
    metas = extract_ide_metas(layout)
    cols = assemble_columns(layout)
    pcs: list[PageColumn] = []
    for col, m in zip(cols, metas, strict=True):
        pcs.append(PageColumn(
            page_stem=stem, column_index=col.column_index,
            meta=m, column=col,
        ))
    return pcs


@pytest.fixture
def spike_stems() -> list[str]:
    if not _SPIKE_LINES_DIR.exists():
        pytest.skip("age8-probe-basic 数据未生成（需要先跑 OCR probe）")
    return sorted(
        d.name for d in _SPIKE_LINES_DIR.iterdir()
        if (d / "lines.jsonl").exists()
    )


@pytest.fixture
def all_pcs(spike_stems: list[str]) -> list[PageColumn]:
    pcs: list[PageColumn] = []
    for stem in spike_stems:
        pcs.extend(_load_page_columns(stem))
    return pcs


class TestAge8E2eAcceptance:
    """AGE-52 验收断言：spike 8 张端到端"""

    @pytest.mark.asyncio
    async def test_min_files_count(
        self, all_pcs: list[PageColumn], tmp_path: Path,
    ) -> None:
        """断言 1：files/ 下至少 ≥ 3 个文件"""
        sources = group_into_files(all_pcs)
        await render_code_files(sources, tmp_path)
        files_dir = tmp_path / "files"
        emitted = list(files_dir.rglob("*"))
        emitted_files = [p for p in emitted if p.is_file()]
        assert len(emitted_files) >= 3, (
            f"实际 {len(emitted_files)} 个文件 < 3"
        )

    @pytest.mark.asyncio
    async def test_chromium_path_present(
        self, all_pcs: list[PageColumn], tmp_path: Path,
    ) -> None:
        """断言 2：files-index.json 含 chromium 源树路径"""
        sources = group_into_files(all_pcs)
        result = await render_code_files(sources, tmp_path)
        index = json.loads(result.index_path.read_text())
        all_paths = " ".join(e["path"] for e in index)
        assert "media/gpu/openmax" in all_paths, all_paths

    @pytest.mark.asyncio
    async def test_no_ide_ui_in_code(
        self, all_pcs: list[PageColumn], tmp_path: Path,
    ) -> None:
        """断言 3：源文件内容不含 IDE UI 关键字（PROBLEMS / 田田田田 等）"""
        sources = group_into_files(all_pcs)
        await render_code_files(sources, tmp_path)
        for f in (tmp_path / "files").rglob("*"):
            if not f.is_file():
                continue
            content = f.read_text(encoding="utf-8")
            for token in _FORBIDDEN_UI_TOKENS:
                assert token not in content, (
                    f"{f.relative_to(tmp_path)} 含 IDE UI 关键字 {token!r}"
                )

    @pytest.mark.asyncio
    async def test_no_line_number_prefix(
        self, all_pcs: list[PageColumn], tmp_path: Path,
    ) -> None:
        """断言 4：源文件首行不是 ``\\d+ `` 行号前缀（行号已被 ide_layout 剥离）"""
        sources = group_into_files(all_pcs)
        await render_code_files(sources, tmp_path)
        line_no_prefix = re.compile(r"^\s*\d+\s+\S")
        for f in (tmp_path / "files").rglob("*"):
            if not f.is_file():
                continue
            content = f.read_text(encoding="utf-8")
            first = content.split("\n", 1)[0]
            assert not line_no_prefix.match(first), (
                f"{f.relative_to(tmp_path)} 首行像行号前缀: {first!r}"
            )

    @pytest.mark.asyncio
    async def test_indent_preserved(
        self, all_pcs: list[PageColumn], tmp_path: Path,
    ) -> None:
        """断言 5：源文件中至少有部分行带前导空格（缩进保留）"""
        sources = group_into_files(all_pcs)
        await render_code_files(sources, tmp_path)
        for f in (tmp_path / "files").rglob("*"):
            if not f.is_file():
                continue
            content = f.read_text(encoding="utf-8")
            indented_lines = [
                line for line in content.splitlines()
                if line.startswith(" ") or line.startswith("\t")
            ]
            assert indented_lines, (
                f"{f.relative_to(tmp_path)} 全无缩进，spike 几乎不可能"
            )

    @pytest.mark.asyncio
    async def test_files_index_complete(
        self, all_pcs: list[PageColumn], tmp_path: Path,
    ) -> None:
        """断言：files-index.json 每条 entry 字段完整"""
        sources = group_into_files(all_pcs)
        result = await render_code_files(sources, tmp_path)
        index = json.loads(result.index_path.read_text())
        required = {"path", "filename", "language", "source_pages",
                    "line_count", "line_no_range", "flags"}
        for entry in index:
            assert required.issubset(entry.keys()), (
                f"index entry {entry} 缺 {required - entry.keys()}"
            )

    @pytest.mark.asyncio
    async def test_known_files_recovered(
        self, all_pcs: list[PageColumn], tmp_path: Path,
    ) -> None:
        """断言：spike 已知的 4+ 文件应都被恢复

        spike 8 张含：openmax_video_decode_accelerator.cc/.h, openmax_status.h,
        gles2_dmabuf_to_egl_image_translator.cc/.h, BUILD.gn
        """
        sources = group_into_files(all_pcs)
        await render_code_files(sources, tmp_path)
        all_paths = sorted(
            str(p.relative_to(tmp_path / "files"))
            for p in (tmp_path / "files").rglob("*") if p.is_file()
        )
        joined = " ".join(all_paths).lower()
        # 关键文件存在（filename 大小写宽松）
        for keyword in [
            "openmax_video_decode_accelerator",
            "openmax_status.h",
            "gles2_dmabuf_to_egl_image_translator",
            "build.gn",
        ]:
            assert keyword in joined, f"未找到 {keyword}: {all_paths}"
