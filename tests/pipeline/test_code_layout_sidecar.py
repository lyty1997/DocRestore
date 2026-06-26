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

"""代码模式版面 sidecar ``.code_layout.json`` 落盘测试（#93 · B2）。

直接驱动 ``Pipeline._write_code_layout_sidecar``（``_code_pipeline`` 收尾时调用的
同一方法）：断言逐行 bbox + page 标识保真、重叠区按 provenance 取胜出页、非 VL
（无行 bbox）不落盘。合成 SourceFile + 派生断言。
"""

from __future__ import annotations

from pathlib import Path

from docrestore.output.code_layout_sidecar import (
    CODE_LAYOUT_FILENAME,
    load_code_layout,
)
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import PageColumn, SourceFile
from docrestore.processing.ide_meta_extract import IDEMeta

Bbox = tuple[int, int, int, int]


def _cfg() -> PipelineConfig:
    """关精修配置（与 sidecar 落盘无关，仅满足 Pipeline 构造）。"""
    return PipelineConfig(
        llm=LLMConfig(model="stub", enable_refine=False, enable_cache=False),
    )


def _page(stem: str, col_index: int, lines: list[CodeLine]) -> PageColumn:
    """构造一页一栏。"""
    return PageColumn(
        page_stem=stem,
        column_index=col_index,
        meta=IDEMeta(column_index=col_index),
        column=CodeColumn(
            column_index=col_index, bbox=(0, 0, 100, 100), code_text="",
            lines=lines, char_width=10.0, avg_line_height=20,
        ),
    )


def _line(line_no: int, bbox: Bbox | None) -> CodeLine:
    """构造一行；bbox=None 表示推断行 / gap 占位。"""
    return CodeLine(line_no=line_no, text=f"l{line_no}", indent=0, bbox=bbox)


def _source(
    path: str, pages: list[PageColumn], *, provenance: dict[int, str] | None = None,
) -> SourceFile:
    """构造一个 SourceFile（仅落盘关心 path/pages/line_provenance）。"""
    return SourceFile(
        path=path, filename=path.rsplit("/", 1)[-1], language="python",
        pages=pages, merged_text="", line_count=0, line_no_range=(1, 1),
        line_provenance=provenance or {},
    )


async def test_sidecar_written_with_line_bbox_and_page(tmp_path: Path) -> None:
    """落 .code_layout.json：逐行 line_no/page/bbox 保真，按 line_no 排序。"""
    out = tmp_path / "out"
    out.mkdir()
    src = _source("app/foo.py", [
        _page("page0001", 0, [
            _line(2, (10, 40, 200, 60)), _line(1, (10, 20, 200, 40)),
        ]),
    ])

    pipeline = Pipeline(_cfg())
    await pipeline._write_code_layout_sidecar([src], out)

    layout = load_code_layout(out)
    assert layout is not None
    assert [f.path for f in layout.files] == ["app/foo.py"]
    lines = layout.files[0].lines
    assert [box.line_no for box in lines] == [1, 2]
    assert all(box.page == "page0001.col0" for box in lines)
    assert lines[0].bbox == (10, 20, 200, 40)


async def test_overlap_uses_provenance_winner(tmp_path: Path) -> None:
    """同 line_no 拍两张（重叠区）→ 取 line_provenance 指定胜出页的 bbox（去重）。"""
    out = tmp_path / "out"
    out.mkdir()
    src = _source("a.py", [
        _page("pa", 0, [_line(5, (0, 0, 1, 1))]),
        _page("pb", 0, [_line(5, (9, 9, 9, 9))]),
    ], provenance={5: "pb"})

    pipeline = Pipeline(_cfg())
    await pipeline._write_code_layout_sidecar([src], out)

    layout = load_code_layout(out)
    assert layout is not None
    lines = layout.files[0].lines
    assert len(lines) == 1
    assert lines[0].page == "pb.col0"
    assert lines[0].bbox == (9, 9, 9, 9)


async def test_no_sidecar_when_no_line_bbox(tmp_path: Path) -> None:
    """非 VL 引擎（无任何行 bbox）→ 不落 sidecar，前端无数据不放大。"""
    out = tmp_path / "out"
    out.mkdir()
    src = _source("a.py", [_page("p1", 0, [_line(1, None), _line(2, None)])])

    pipeline = Pipeline(_cfg())
    await pipeline._write_code_layout_sidecar([src], out)

    assert load_code_layout(out) is None
    assert not (out / CODE_LAYOUT_FILENAME).exists()
