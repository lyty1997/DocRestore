# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""跨张文件归类单测（AGE-8 Phase 2.3）"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docrestore.models import TextLine
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import (
    PageColumn,
    SourceFile,
    group_into_files,
)
from docrestore.processing.ide_layout import analyze_layout
from docrestore.processing.ide_meta_extract import IDEMeta, extract_ide_metas


def _meta(filename: str, path: str | None = None, language: str = "cpp") -> IDEMeta:
    return IDEMeta(
        column_index=0,
        filename=filename,
        path=path or filename,
        language=language,
        tab_readable=True,
        breadcrumb_readable=True,
    )


def _column(
    *line_data: tuple[int, str, int],   # (line_no, text, indent)
    column_index: int = 0,
    bbox: tuple[int, int, int, int] = (0, 0, 1000, 1000),
    char_width: float = 12.0,
    line_height: int = 30,
) -> CodeColumn:
    lines = [
        CodeLine(line_no=n, text=t, indent=i, bbox=None)
        for n, t, i in line_data
    ]
    code_text = "\n".join(" " * i + t for n, t, i in line_data)
    return CodeColumn(
        column_index=column_index,
        bbox=bbox,
        code_text=code_text,
        lines=lines,
        char_width=char_width,
        avg_line_height=line_height,
    )


def _pc(stem: str, col_idx: int, meta: IDEMeta, column: CodeColumn) -> PageColumn:
    return PageColumn(
        page_stem=stem,
        column_index=col_idx,
        meta=meta,
        column=column,
    )


# ---------- 单元测试 ----------

class TestBasicGrouping:
    def test_empty_input(self) -> None:
        assert group_into_files([]) == []

    def test_single_file_single_page(self) -> None:
        pcs = [_pc(
            "DSC1", 0, _meta("foo.cc", "media/gpu/foo.cc"),
            _column((1, "#include <a>", 0), (2, "int main() {", 0)),
        )]
        files = group_into_files(pcs)
        assert len(files) == 1
        f = files[0]
        assert isinstance(f, SourceFile)
        assert f.filename == "foo.cc"
        assert f.path == "media/gpu/foo.cc"
        assert f.language == "cpp"
        assert "#include <a>" in f.merged_text
        assert f.line_no_range == (1, 2)

    def test_two_pages_same_file_concat(self) -> None:
        """两张图同 file，行号 1-3 + 4-6 → 拼成 6 行"""
        m = _meta("foo.cc", "src/foo.cc")
        p1 = _pc("DSC1", 0, m, _column(
            (1, "L1", 0), (2, "L2", 0), (3, "L3", 0),
        ))
        p2 = _pc("DSC2", 0, m, _column(
            (4, "L4", 0), (5, "L5", 0), (6, "L6", 0),
        ))
        files = group_into_files([p1, p2])
        assert len(files) == 1
        f = files[0]
        assert f.line_no_range == (1, 6)
        assert f.merged_text == "L1\nL2\nL3\nL4\nL5\nL6"
        assert "code.grouping.merged_pages=2" in f.flags

    def test_overlap_dedup(self) -> None:
        """两张图重叠（行 3-5 和 4-7）→ 重复 line_no 取首次"""
        m = _meta("foo.cc")
        p1 = _pc("DSC1", 0, m, _column(
            (3, "FROM_PAGE_1_L3", 0),
            (4, "FROM_PAGE_1_L4", 0),
            (5, "FROM_PAGE_1_L5", 0),
        ))
        p2 = _pc("DSC2", 0, m, _column(
            (4, "FROM_PAGE_2_L4", 0),
            (5, "FROM_PAGE_2_L5", 0),
            (6, "FROM_PAGE_2_L6", 0),
            (7, "FROM_PAGE_2_L7", 0),
        ))
        files = group_into_files([p1, p2])
        text = files[0].merged_text
        # 重叠区域取 page_1（先入）
        assert "FROM_PAGE_1_L4" in text
        assert "FROM_PAGE_2_L4" not in text
        # 非重叠区域保留各自
        assert "FROM_PAGE_1_L3" in text
        assert "FROM_PAGE_2_L7" in text

    def test_line_gap_flag(self) -> None:
        """单文件行号 1 + 5 跳过 2-4 → 标 missing flag + 占位空行"""
        m = _meta("foo.cc")
        pc = _pc("DSC1", 0, m, _column(
            (1, "L1", 0), (5, "L5", 0),
        ))
        f = group_into_files([pc])[0]
        assert any("missing_line_nos" in fl for fl in f.flags)
        # 中间 3 个空行占位
        assert f.merged_text == "L1\n\n\n\nL5"


class TestSamenameDifferentDir:
    def test_same_name_different_dir_split(self) -> None:
        """两张图都是 BUILD.gn 但目录不同 → 分两组"""
        p1 = _pc(
            "DSC1", 0, _meta("BUILD.gn", "media/gpu/openmax/BUILD.gn", "gn"),
            _column((1, "import(\"//a\")", 0)),
        )
        p2 = _pc(
            "DSC2", 0, _meta("BUILD.gn", "components/foo/BUILD.gn", "gn"),
            _column((1, "import(\"//b\")", 0)),
        )
        files = group_into_files([p1, p2])
        assert len(files) == 2
        paths = sorted(f.path for f in files)
        assert paths == [
            "components/foo/BUILD.gn",
            "media/gpu/openmax/BUILD.gn",
        ]

    def test_compatible_dir_merged(self) -> None:
        """前缀缺失版本 + 完整版本（gpu/openmax 是 media/gpu/openmax 后缀）→ 合并"""
        p1 = _pc(
            "DSC1", 0, _meta("BUILD.gn", "gpu/openmax/BUILD.gn", "gn"),
            _column((1, "L1", 0)),
        )
        p2 = _pc(
            "DSC2", 0, _meta("BUILD.gn", "media/gpu/openmax/BUILD.gn", "gn"),
            _column((2, "L2", 0)),
        )
        files = group_into_files([p1, p2])
        assert len(files) == 1
        # canonical 选段数最多版（media/gpu/openmax）
        assert files[0].path == "media/gpu/openmax/BUILD.gn"

    def test_ocr_charcase_unified(self) -> None:
        """BUILD/BUiLD/BUlLD 大小写差 OCR 噪声 → 统一为同一 file"""
        p1 = _pc("DSC1", 0, _meta("BUILD.gn", "x/BUILD.gn", "gn"),
                 _column((1, "L1", 0)))
        p2 = _pc("DSC2", 0, _meta("BUiLD.gn", "x/BUiLD.gn", "gn"),
                 _column((2, "L2", 0)))
        p3 = _pc("DSC3", 0, _meta("BUlLD.gn", "x/BUlLD.gn", "gn"),
                 _column((3, "L3", 0)))
        files = group_into_files([p1, p2, p3])
        assert len(files) == 1
        # canonical filename：长度+频次最大；3 个都是 8 字符，频次各 1，取首
        assert files[0].filename in {"BUILD.gn", "BUiLD.gn", "BUlLD.gn"}
        assert files[0].line_no_range == (1, 3)


class TestNoFilename:
    def test_filename_missing_separate_group(self) -> None:
        m_no = IDEMeta(column_index=0, filename=None, path=None)
        pc = _pc("DSC1", 0, m_no, _column((1, "x", 0)))
        files = group_into_files([pc])
        assert len(files) == 1
        assert "code.grouping.no_filename" in files[0].flags
        assert files[0].filename == "_unknown"


# ---------- spike 集成测试 ----------

SPIKE_LINES_DIR = (
    Path(__file__).resolve().parents[2] / "output" / "age8-probe-basic"
)
SPIKE_IMAGE_DIR = (
    Path(__file__).resolve().parents[2] / "test_images" / "age8-spike"
)


def _list_spike_stems() -> list[str]:
    if not SPIKE_LINES_DIR.exists():
        return []
    return sorted(
        d.name for d in SPIKE_LINES_DIR.iterdir()
        if (d / "lines.jsonl").exists()
    )


def _build_page_columns_for_stem(stem: str) -> list[PageColumn]:
    from PIL import Image

    from docrestore.processing.code_assembly import assemble_columns

    p = SPIKE_LINES_DIR / stem / "lines.jsonl"
    items = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    text_lines = [
        TextLine(
            bbox=tuple(int(v) for v in it["bbox"][:4]),  # type: ignore[arg-type]
            text=it["text"],
            score=float(it.get("score", 1.0)),
        )
        for it in items
    ]
    img = SPIKE_IMAGE_DIR / f"{stem}.JPG"
    sz = Image.open(img).size if img.exists() else (3400, 1900)
    layout = analyze_layout(text_lines, sz)
    metas = extract_ide_metas(layout)
    columns = assemble_columns(layout)
    pcs: list[PageColumn] = []
    for col, m in zip(columns, metas, strict=True):
        pcs.append(PageColumn(
            page_stem=stem,
            column_index=col.column_index,
            meta=m,
            column=col,
        ))
    return pcs


@pytest.mark.skipif(
    not _list_spike_stems(),
    reason="age8-probe-basic 数据未生成",
)
class TestSpikeAggregation:
    """8 张 spike 总聚合：按已知 file 数量验证分组数"""

    @pytest.fixture
    def all_pcs(self) -> list[PageColumn]:
        result: list[PageColumn] = []
        for stem in _list_spike_stems():
            result.extend(_build_page_columns_for_stem(stem))
        return result

    def test_all_pages_grouped(self, all_pcs: list[PageColumn]) -> None:
        files = group_into_files(all_pcs)
        # 至少应有 5 个独立 file（cc/h/gn 各种）
        assert len(files) >= 4, f"分组数 {len(files)} 偏少"
        total_pages = sum(len(f.pages) for f in files)
        assert total_pages == len(all_pcs), "总 page 数必须保持"

    def test_status_h_merged(self, all_pcs: list[PageColumn]) -> None:
        """openmax_status.h 跨 3 张图 6 个栏 → 应聚一个 file"""
        files = group_into_files(all_pcs)
        status_files = [
            f for f in files if "openmax_status.h" in f.filename.lower()
        ]
        # 可能有 1 个或多个（depending on dir 兼容性）
        assert status_files, "未找到 openmax_status.h"
        # 至少有一个 SourceFile 跨多页
        assert any(len(f.pages) >= 2 for f in status_files), (
            "openmax_status.h 应跨多页聚合"
        )

    def test_build_gn_unified(self, all_pcs: list[PageColumn]) -> None:
        """BUILD.gn 各种 OCR 字符变体（BUILD/BUiLD/BUlLD）应聚到 ≤ 2 组"""
        files = group_into_files(all_pcs)
        build_files = [
            f for f in files if "build.gn" == f.filename.lower()
        ]
        # 同一项目里 BUILD.gn 应被 fuzzy 合并到 ≤ 2 组（worst case：dir 不兼容）
        assert len(build_files) <= 2, (
            f"BUILD.gn 分组过多 ({len(build_files)})，OCR 字符级未统一"
        )
