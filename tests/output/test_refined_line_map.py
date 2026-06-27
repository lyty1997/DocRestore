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

"""#5 代码放大镜行映射：``build_refined_line_map`` + sidecar ``line_map`` 序列化。

覆盖 difflib 行对齐（仅 equal 段精确映回原 OCR line_no、改写/新增行 None）、守恒→空、
base_line_no 偏移、``split("\\n")`` 末尾空行口径；以及 sidecar line_map 的 to/from_dict
向后兼容（缺键→[]、坏元素→None、非 list→[]）。断言全部从构造输入派生。
"""

from __future__ import annotations

from docrestore.output.code_layout_sidecar import (
    CodeFileLayout,
    CodeLayout,
    CodeLineBox,
    build_code_layout,
    build_refined_line_map,
    from_dict,
    to_dict,
)
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import PageColumn, SourceFile
from docrestore.processing.ide_meta_extract import IDEMeta

Bbox = tuple[int, int, int, int]


class TestBuildRefinedLineMap:
    """difflib 行对齐：equal 精确映回原 line_no、改写/新增行 None、守恒→空。"""

    def test_identity_unchanged_returns_empty(self) -> None:
        """原文 == 精修文 → []（identity 信号，前端走 displayLineNumber 直查）。"""
        text = "a\nb\nc"
        assert build_refined_line_map(text, text, 10) == []

    def test_delete_middle_line_equal_blocks_map(self) -> None:
        """删中间行：剩余两行精确映回各自原 OCR line_no（base=10）。"""
        # 原 ['a','b','c'] line_no 10/11/12 → 删 'b' → ['a','c']
        assert build_refined_line_map("a\nb\nc", "a\nc", 10) == [10, 12]

    def test_inserted_line_is_none(self) -> None:
        """新增行 → None；前后未改行精确映回。"""
        # 原 ['a','c'](10/11) → 插 'b' → ['a','b','c']
        assert build_refined_line_map("a\nc", "a\nb\nc", 10) == [10, None, 11]

    def test_replaced_line_is_none(self) -> None:
        """改写行 → None（不放大优于错放）；未改行精确映回。"""
        assert build_refined_line_map("a\nb\nc", "a\nXX\nc", 10) == [10, None, 12]

    def test_base_line_no_offset_applied(self) -> None:
        """值=base_line_no + 原行下标；末尾追加行无原行 → None。"""
        assert build_refined_line_map("a\nb", "a\nb\nc", 100) == [100, 101, None]

    def test_trailing_newline_split_consistency(self) -> None:
        """末尾换行按 split('\\n') 产空尾行（与前端 splitEditorLines 同口径）→ None。"""
        # 'a\nb' -> ['a','b']; 'a\nb\n' -> ['a','b','']
        assert build_refined_line_map("a\nb", "a\nb\n", 10) == [10, 11, None]

    def test_length_matches_refined_line_count(self) -> None:
        """返回长度恒 == 精修文 split('\\n') 行数（前端按显示行序索引）。"""
        refined = "a\nXX\nYY\nc\nd"
        result = build_refined_line_map("a\nb\nc\nd", refined, 1)
        assert len(result) == len(refined.split("\n"))


class TestLineMapSerialization:
    """sidecar line_map 的 to/from_dict：非空才输出、round-trip、宽松容损。"""

    @staticmethod
    def _file(line_map: list[int | None]) -> CodeFileLayout:
        return CodeFileLayout(
            path="a.py",
            lines=[CodeLineBox(line_no=1, page="p.col0", bbox=(0, 0, 1, 1))],
            line_map=line_map,
        )

    def test_to_dict_includes_line_map_when_present(self) -> None:
        payload = to_dict(CodeLayout(files=[self._file([1, None, 3])]))
        files = payload["files"]
        assert isinstance(files, list)
        assert files[0]["line_map"] == [1, None, 3]

    def test_to_dict_omits_line_map_when_empty(self) -> None:
        """空 line_map 不输出该键（旧任务 / 守恒场景体积兼容）。"""
        payload = to_dict(CodeLayout(files=[self._file([])]))
        files = payload["files"]
        assert isinstance(files, list)
        assert "line_map" not in files[0]

    def test_round_trip_preserves_line_map(self) -> None:
        restored = from_dict(to_dict(CodeLayout(files=[self._file([1, None, 5])])))
        assert restored is not None
        assert restored.files[0].line_map == [1, None, 5]

    def test_from_dict_missing_key_is_empty(self) -> None:
        """旧 sidecar 无 line_map 键 → []（identity 回退）。"""
        data = {
            "version": 1,
            "files": [{
                "path": "a.py",
                "lines": [{"line_no": 1, "page": "p.col0", "bbox": [0, 0, 1, 1]}],
            }],
        }
        restored = from_dict(data)
        assert restored is not None
        assert restored.files[0].line_map == []

    def test_from_dict_bad_elements_become_none(self) -> None:
        """坏元素（字符串 / bool）就地置 None，int 保留，不整文件失败。"""
        data = {
            "version": 1,
            "files": [{
                "path": "a.py",
                "lines": [{"line_no": 1, "page": "p.col0", "bbox": [0, 0, 1, 1]}],
                "line_map": [1, "x", None, True, 2],
            }],
        }
        restored = from_dict(data)
        assert restored is not None
        assert restored.files[0].line_map == [1, None, None, None, 2]

    def test_from_dict_non_list_line_map_is_empty(self) -> None:
        data = {
            "version": 1,
            "files": [{
                "path": "a.py",
                "lines": [{"line_no": 1, "page": "p.col0", "bbox": [0, 0, 1, 1]}],
                "line_map": "garbage",
            }],
        }
        restored = from_dict(data)
        assert restored is not None
        assert restored.files[0].line_map == []


def _page(stem: str, lines: list[CodeLine]) -> PageColumn:
    """最小一页一栏。"""
    return PageColumn(
        page_stem=stem,
        column_index=0,
        meta=IDEMeta(column_index=0),
        column=CodeColumn(
            column_index=0,
            bbox=(0, 0, 100, 100),
            code_text="",
            lines=lines,
            char_width=10.0,
            avg_line_height=20,
        ),
    )


def test_build_code_layout_carries_refined_line_map() -> None:
    """build_code_layout 把 src.refined_line_map 透传进 CodeFileLayout.line_map。"""
    src = SourceFile(
        path="a.py",
        filename="a.py",
        language="python",
        pages=[
            _page("p1", [CodeLine(line_no=1, text="x", indent=0, bbox=(0, 0, 1, 1))]),
        ],
        merged_text="",
        line_count=0,
        line_no_range=(1, 1),
        refined_line_map=[1, None],
    )
    layout = build_code_layout([src])
    assert layout is not None
    assert layout.files[0].line_map == [1, None]
