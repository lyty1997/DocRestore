# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""代码渲染器单测（AGE-8 Phase 2.4）"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docrestore.output.code_renderer import (
    CodeRenderResult,
    render_code_files,
)
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import PageColumn, SourceFile
from docrestore.processing.ide_meta_extract import IDEMeta


def _make_source(
    path: str,
    code_text: str = "int main() {}\n",
    *,
    language: str = "cpp",
    flags: list[str] | None = None,
    page_stems: list[str] | None = None,
) -> SourceFile:
    pages = []
    for stem in page_stems or ["DSC1"]:
        meta = IDEMeta(
            column_index=0, filename=path.rsplit("/", 1)[-1],
            path=path, language=language,
            tab_readable=True, breadcrumb_readable=True,
        )
        col = CodeColumn(
            column_index=0, bbox=(0, 0, 1, 1),
            code_text=code_text,
            lines=[CodeLine(line_no=1, text=code_text.rstrip(), indent=0)],
            char_width=12.0, avg_line_height=30,
        )
        pages.append(PageColumn(
            page_stem=stem, column_index=0, meta=meta, column=col,
        ))
    return SourceFile(
        path=path,
        filename=path.rsplit("/", 1)[-1],
        language=language,
        pages=pages,
        merged_text=code_text.rstrip("\n"),
        line_count=code_text.count("\n") + 1,
        line_no_range=(1, code_text.count("\n") + 1),
        flags=flags or [],
    )


# ---------- 单元测试 ----------

class TestBasicRender:
    @pytest.mark.asyncio
    async def test_writes_files_and_index(self, tmp_path: Path) -> None:
        sources = [
            _make_source("media/gpu/openmax/foo.cc", "// foo\nint x;\n"),
            _make_source(
                "media/gpu/openmax/foo.h", "#pragma once\n",
                language="cpp",
            ),
            _make_source(
                "media/gpu/openmax/BUILD.gn",
                'import("//build")\n', language="gn",
            ),
        ]
        result = await render_code_files(sources, tmp_path)
        assert isinstance(result, CodeRenderResult)
        assert (tmp_path / "files" / "media/gpu/openmax/foo.cc").exists()
        assert (tmp_path / "files" / "media/gpu/openmax/foo.h").exists()
        assert (tmp_path / "files" / "media/gpu/openmax/BUILD.gn").exists()
        assert result.index_path.exists()
        assert result.document_path.exists()
        assert len(result.written_files) == 3

    @pytest.mark.asyncio
    async def test_index_fields_complete(self, tmp_path: Path) -> None:
        sources = [_make_source(
            "src/foo.cc", "int x;\n",
            page_stems=["DSC1", "DSC2"],
            flags=["code.grouping.merged_pages=2"],
        )]
        result = await render_code_files(sources, tmp_path)
        index = json.loads(result.index_path.read_text())
        assert len(index) == 1
        entry = index[0]
        assert entry["path"] == "src/foo.cc"
        assert entry["filename"] == "foo.cc"
        assert entry["language"] == "cpp"
        assert "DSC1.col0" in entry["source_pages"]
        assert "DSC2.col0" in entry["source_pages"]
        assert "code.grouping.merged_pages=2" in entry["flags"]
        assert entry["line_no_range"][0] == 1

    @pytest.mark.asyncio
    async def test_file_content_preserved(self, tmp_path: Path) -> None:
        text = '#include "x.h"\nint main() { return 0; }\n'
        sources = [_make_source("a/b/foo.cc", text)]
        await render_code_files(sources, tmp_path)
        actual = (tmp_path / "files/a/b/foo.cc").read_text()
        # 写入会保证末尾换行
        assert actual.startswith('#include "x.h"')
        assert "int main() { return 0; }" in actual
        assert actual.endswith("\n")

    @pytest.mark.asyncio
    async def test_document_md_compat(self, tmp_path: Path) -> None:
        sources = [
            _make_source("a/foo.cc", "// foo\n", language="cpp"),
            _make_source("a/bar.gn", 'a = "b"\n', language="gn"),
        ]
        result = await render_code_files(sources, tmp_path)
        doc = result.document_path.read_text()
        assert "## `a/foo.cc`" in doc
        assert "## `a/bar.gn`" in doc
        assert "```cpp" in doc
        assert "```gn" in doc


class TestPathSafety:
    @pytest.mark.asyncio
    async def test_traversal_rejected(self, tmp_path: Path) -> None:
        """`..` 段必须被拒绝并降级到 _unknown/"""
        sources = [
            _make_source("../etc/passwd", "evil\n"),
        ]
        result = await render_code_files(sources, tmp_path)
        # 不应写到 tmp_path 之外
        assert not (tmp_path.parent / "etc" / "passwd").exists()
        # 应降级到 _unknown/
        assert (tmp_path / "files" / "_unknown" / "passwd").exists()
        # skipped 记录
        assert any(
            reason == "traversal" for _, reason in result.skipped
        )

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        sources = [_make_source("/etc/passwd", "evil\n")]
        result = await render_code_files(sources, tmp_path)
        assert (tmp_path / "files" / "_unknown" / "passwd").exists()
        assert any(reason == "absolute" for _, reason in result.skipped)

    @pytest.mark.asyncio
    async def test_empty_path_rejected(self, tmp_path: Path) -> None:
        sources = [_make_source("", "x\n")]
        result = await render_code_files(sources, tmp_path)
        assert (tmp_path / "files" / "_unknown" / "_empty").exists()
        assert any(reason == "empty" for _, reason in result.skipped)


class TestEmptyInput:
    @pytest.mark.asyncio
    async def test_no_sources(self, tmp_path: Path) -> None:
        result = await render_code_files([], tmp_path)
        # 索引仍写出（空数组）
        assert result.index_path.exists()
        assert json.loads(result.index_path.read_text()) == []
        assert result.written_files == []


_SPIKE_LINES_DIR = (
    Path(__file__).resolve().parents[2] / "output" / "age8-probe-basic"
)
_SPIKE_IMAGE_DIR = (
    Path(__file__).resolve().parents[2] / "test_images" / "age8-spike"
)


class TestSpikeIntegration:
    """端到端集成：spike → IDELayout → ide_meta → assemble → grouping → render"""

    @pytest.mark.asyncio
    async def test_spike_full_pipeline(self, tmp_path: Path) -> None:
        if not _SPIKE_LINES_DIR.exists():
            pytest.skip("age8-probe-basic 数据未生成")

        from PIL import Image

        from docrestore.models import TextLine
        from docrestore.processing.code_assembly import assemble_columns
        from docrestore.processing.code_file_grouping import (
            PageColumn,
            group_into_files,
        )
        from docrestore.processing.ide_layout import analyze_layout
        from docrestore.processing.ide_meta_extract import extract_ide_metas

        spike_lines_dir = _SPIKE_LINES_DIR
        spike_image_dir = _SPIKE_IMAGE_DIR

        all_pcs: list[PageColumn] = []
        for d in sorted(spike_lines_dir.iterdir()):
            if not (d / "lines.jsonl").exists():
                continue
            stem = d.name
            items = [
                json.loads(line)
                for line in (d / "lines.jsonl").read_text().splitlines()
                if line.strip()
            ]
            text_lines = [
                TextLine(
                    bbox=tuple(int(v) for v in it["bbox"][:4]),  # type: ignore[arg-type]
                    text=it["text"],
                    score=float(it.get("score", 1.0)),
                )
                for it in items
            ]
            img = spike_image_dir / f"{stem}.JPG"
            sz = Image.open(img).size if img.exists() else (3400, 1900)
            layout = analyze_layout(text_lines, sz)
            metas = extract_ide_metas(layout)
            cols = assemble_columns(layout)
            for c, m in zip(cols, metas, strict=True):
                all_pcs.append(PageColumn(
                    page_stem=stem, column_index=c.column_index,
                    meta=m, column=c,
                ))

        sources = group_into_files(all_pcs)
        result = await render_code_files(sources, tmp_path)

        # 验证：写出的文件数 = SourceFile 数（spike 实测 5 个）
        assert len(result.written_files) == len(sources)
        assert len(result.written_files) >= 4

        # 索引 JSON 合法
        index = json.loads(result.index_path.read_text())
        assert len(index) == len(sources)

        # document.md 含每个文件
        doc = result.document_path.read_text()
        for src in sources:
            assert src.path in doc

        # 文件内容非空
        for f in result.written_files:
            assert f.read_text(), f"{f} 内容空"
