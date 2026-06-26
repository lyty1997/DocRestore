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

"""代码版面 sidecar ``code_layout_sidecar`` 纯模块测试（#93 · B1）。

覆盖：``build_code_layout`` 收行级 bbox / 跳过无 bbox 行 / 重叠区按 provenance
取胜出页 + 胜出页无 bbox 回退 / 全无 bbox → None；round-trip 保真；反序列化
「坏行跳过、坏文件跳过、不整份失败」宽松 fail-safe；磁盘 I/O。
"""

from __future__ import annotations

import json
from pathlib import Path

from docrestore.output.code_layout_sidecar import (
    CODE_LAYOUT_FILENAME,
    CodeFileLayout,
    CodeLayout,
    CodeLineBox,
    build_code_layout,
    from_dict,
    load_code_layout,
    to_dict,
    write_code_layout,
)
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import PageColumn, SourceFile
from docrestore.processing.ide_meta_extract import IDEMeta

Bbox = tuple[int, int, int, int]


def _line(line_no: int, bbox: Bbox | None) -> CodeLine:
    """构造一行：bbox=None 表示推断行 / gap 占位（应被跳过）。"""
    return CodeLine(line_no=line_no, text=f"line{line_no}", indent=0, bbox=bbox)


def _page(stem: str, col_index: int, lines: list[CodeLine]) -> PageColumn:
    """构造一页一栏（meta 仅 build 不读，最小构造）。"""
    return PageColumn(
        page_stem=stem,
        column_index=col_index,
        meta=IDEMeta(column_index=col_index),
        column=CodeColumn(
            column_index=col_index,
            bbox=(0, 0, 100, 100),
            code_text="",
            lines=lines,
            char_width=10.0,
            avg_line_height=20,
        ),
    )


def _source(
    path: str,
    pages: list[PageColumn],
    *,
    provenance: dict[int, str] | None = None,
) -> SourceFile:
    """构造一个 SourceFile（仅 build 关心 path/pages/line_provenance）。"""
    return SourceFile(
        path=path,
        filename=path.rsplit("/", 1)[-1],
        language="python",
        pages=pages,
        merged_text="",
        line_count=0,
        line_no_range=(1, 1),
        line_provenance=provenance or {},
    )


def test_collects_bbox_lines_sorted_with_page_id() -> None:
    """单文件：收 bbox 行、按 line_no 排序、page=={stem}.col{idx}。"""
    src = _source("app/foo.py", [
        _page("p1", 0, [_line(3, (0, 30, 10, 40)), _line(1, (0, 10, 10, 20))]),
    ])
    layout = build_code_layout([src])
    assert layout is not None
    lines = layout.files[0].lines
    assert [box.line_no for box in lines] == [1, 3]
    assert all(box.page == "p1.col0" for box in lines)
    assert lines[0].bbox == (0, 10, 10, 20)


def test_skips_lines_without_bbox() -> None:
    """无 bbox 的行（推断行 / gap 占位）跳过，不进 sidecar。"""
    src = _source("a.py", [
        _page("p1", 0, [_line(1, (0, 0, 1, 1)), _line(2, None)]),
    ])
    layout = build_code_layout([src])
    assert layout is not None
    assert [box.line_no for box in layout.files[0].lines] == [1]


def test_page_id_uses_column_index() -> None:
    """page 标识带真实列号（右栏 col1）。"""
    src = _source("a.py", [_page("p9", 1, [_line(5, (1, 2, 3, 4))])])
    layout = build_code_layout([src])
    assert layout is not None
    assert layout.files[0].lines[0].page == "p9.col1"


def test_overlap_prefers_provenance_winner() -> None:
    """同 line_no 两页都有 bbox → 取 line_provenance 指定胜出页（去重）。"""
    pa = _page("pa", 0, [_line(5, (0, 0, 1, 1))])
    pb = _page("pb", 0, [_line(5, (9, 9, 9, 9))])
    src = _source("a.py", [pa, pb], provenance={5: "pb"})
    layout = build_code_layout([src])
    assert layout is not None
    lines = layout.files[0].lines
    assert len(lines) == 1
    assert lines[0].page == "pb.col0"
    assert lines[0].bbox == (9, 9, 9, 9)


def test_overlap_winner_without_bbox_falls_back() -> None:
    """胜出页该行无 bbox（推断行）→ 回退到另一页的 bbox，不丢该行。"""
    winner = _page("pb", 0, [_line(5, None)])  # 胜出页该行无 bbox
    other = _page("pa", 0, [_line(5, (2, 2, 2, 2))])
    src = _source("a.py", [winner, other], provenance={5: "pb"})
    layout = build_code_layout([src])
    assert layout is not None
    lines = layout.files[0].lines
    assert len(lines) == 1
    assert lines[0].page == "pa.col0"
    assert lines[0].bbox == (2, 2, 2, 2)


def test_build_none_when_no_bbox_anywhere() -> None:
    """所有文件均无任何 bbox 行（非 VL 引擎）→ None，调用方不落盘。"""
    src = _source("a.py", [_page("p1", 0, [_line(1, None), _line(2, None)])])
    assert build_code_layout([src]) is None


def test_round_trip_preserves_all_fields() -> None:
    """to_dict → from_dict 保真：path / 每行 line_no/page/bbox 不丢。"""
    layout = CodeLayout(files=[CodeFileLayout(path="a.py", lines=[
        CodeLineBox(1, "p1.col0", (0, 0, 10, 10)),
        CodeLineBox(2, "p1.col0", (0, 10, 10, 20)),
    ])])
    restored = from_dict(to_dict(layout))
    assert restored == layout


def test_from_dict_version_mismatch_returns_none() -> None:
    """版本不符 → None（fail-safe，前端不放大）。"""
    data = to_dict(CodeLayout(files=[CodeFileLayout("a.py", [
        CodeLineBox(1, "p1.col0", (0, 0, 1, 1)),
    ])]))
    data["version"] = 999
    assert from_dict(data) is None


def test_from_dict_skips_bad_line_not_whole_file() -> None:
    """坏行（bbox 维度错 / line_no 非 int）逐个跳过，合法行保留。"""
    data = {
        "version": 1,
        "files": [{
            "path": "a.py",
            "lines": [
                {"line_no": 1, "page": "p.col0", "bbox": [0, 0, 10]},  # 缺一维
                {"line_no": True, "page": "p.col0", "bbox": [0, 0, 1, 1]},  # bool
                {"line_no": 3, "page": "p.col0", "bbox": [0, 0, 5, 5]},  # 好行
            ],
        }],
    }
    layout = from_dict(data)
    assert layout is not None
    assert [box.line_no for box in layout.files[0].lines] == [3]


def test_from_dict_skips_bad_file_not_whole_doc() -> None:
    """坏文件（缺 path）整文件跳过，其余合法文件保留。"""
    data = {
        "version": 1,
        "files": [
            {"lines": []},  # 缺 path
            {"path": "good.py", "lines": [
                {"line_no": 1, "page": "p.col0", "bbox": [0, 0, 5, 5]},
            ]},
        ],
    }
    layout = from_dict(data)
    assert layout is not None
    assert [f.path for f in layout.files] == ["good.py"]


def test_from_dict_none_when_no_valid_file() -> None:
    """无任何合法文件 → None。"""
    assert from_dict({"version": 1, "files": [{"lines": []}]}) is None


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    """write → load 端到端保真，文件名为 .code_layout.json。"""
    layout = CodeLayout(files=[CodeFileLayout("a.py", [
        CodeLineBox(1, "p1.col0", (0, 0, 10, 10)),
    ])])
    path = write_code_layout(tmp_path, layout)
    assert path.name == CODE_LAYOUT_FILENAME
    assert load_code_layout(tmp_path) == layout


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """无 sidecar → None。"""
    assert load_code_layout(tmp_path) is None


def test_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    """损坏 JSON → None（不抛异常）。"""
    (tmp_path / CODE_LAYOUT_FILENAME).write_text("{bad", encoding="utf-8")
    assert load_code_layout(tmp_path) is None


def test_written_json_structure(tmp_path: Path) -> None:
    """落盘 JSON 结构：files[].lines[] 带 line_no/page/bbox。"""
    write_code_layout(tmp_path, CodeLayout(files=[CodeFileLayout("a.py", [
        CodeLineBox(7, "p1.col0", (1, 2, 3, 4)),
    ])]))
    raw = json.loads(
        (tmp_path / CODE_LAYOUT_FILENAME).read_text(encoding="utf-8"),
    )
    line = raw["files"][0]["lines"][0]
    assert line == {"line_no": 7, "page": "p1.col0", "bbox": [1, 2, 3, 4]}
