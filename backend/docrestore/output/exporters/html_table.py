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

"""HTML 表格解析（导出公共件）：``document.md`` 里的表**一律是 HTML ``<table>``**。

xlsx（每表一 sheet）与 pptx（每表一 slide table）都需要把携 ``rowspan/colspan``
的 HTML 表展开成「网格 + 合并区」。这里是两者共用的纯解析层，无第三方依赖。

- :func:`parse_tables`：抽取所有 ``<table>`` → 各表的行（每行 :data:`RawCell` 列表）。
- :func:`build_grid`：按 occupancy 算法展开成 ``cells[(r, c)] = text`` + ``merges``。
- :func:`grid_dimensions`：由 cells/merges 推出网格行列数（建 pptx table 需先知尺寸）。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import TypeAlias

#: 匹配一个 HTML 表格块（与 ``table_dedup.py`` 同口径）
TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)

#: 解析出的一行单元格：(文本, rowspan, colspan)
RawCell: TypeAlias = tuple[str, int, int]
#: 展开后的单元格映射 ``(row, col) -> text``（0 基，仅含合并区左上角）
GridCells: TypeAlias = dict[tuple[int, int], str]
#: 合并区 ``(r1, c1, r2, c2)``（0 基，闭区间）
Merge: TypeAlias = tuple[int, int, int, int]


def clean_text(raw: str) -> str:
    """折叠空白（``convert_charrefs`` 已解码实体）。"""
    return " ".join(raw.split())


def _int_attr(value: str | None) -> int:
    """解析 ``rowspan``/``colspan`` 属性；非法 / 缺失 → 1。"""
    if value is None:
        return 1
    try:
        return max(1, int(value.strip()))
    except ValueError:
        return 1


class _TableHTMLParser(HTMLParser):
    """解析单个 ``<table>`` → ``rows``（每行是 :data:`RawCell` 列表）。"""

    def __init__(self) -> None:
        """初始化解析状态（``convert_charrefs`` 自动解码 HTML 实体）。"""
        super().__init__(convert_charrefs=True)
        self.rows: list[list[RawCell]] = []
        self._cur_row: list[RawCell] | None = None
        self._buf: list[str] = []
        self._in_cell = False
        self._span: tuple[int, int] = (1, 1)

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        """进入行 / 单元格；记录跨行跨列；``<br>`` 折成空格。"""
        name = tag.lower()
        if name == "tr":
            self._cur_row = []
        elif name in {"td", "th"}:
            self._in_cell = True
            self._buf = []
            amap = dict(attrs)
            self._span = (
                _int_attr(amap.get("rowspan")),
                _int_attr(amap.get("colspan")),
            )
        elif name == "br" and self._in_cell:
            self._buf.append(" ")

    def handle_data(self, data: str) -> None:
        """累积当前单元格内的文本（嵌套标签的文本一并归该单元格）。"""
        if self._in_cell:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        """单元格 / 行收尾，落地到 ``rows``。"""
        name = tag.lower()
        if name in {"td", "th"} and self._in_cell:
            rowspan, colspan = self._span
            if self._cur_row is not None:
                self._cur_row.append(
                    (clean_text("".join(self._buf)), rowspan, colspan),
                )
            self._in_cell = False
        elif name == "tr" and self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None


def parse_tables(markdown: str) -> list[list[list[RawCell]]]:
    """抽取 markdown 中所有 ``<table>`` → 各表的 ``rows``（丢弃空表）。"""
    tables: list[list[list[RawCell]]] = []
    for match in TABLE_RE.finditer(markdown):
        tables_rows = parse_one_table(match.group(0))
        if tables_rows:
            tables.append(tables_rows)
    return tables


def parse_one_table(table_html: str) -> list[list[RawCell]]:
    """解析单个 ``<table>...</table>`` 片段 → 非空行列表。"""
    parser = _TableHTMLParser()
    parser.feed(table_html)
    parser.close()
    return [row for row in parser.rows if row]


def build_grid(rows: list[list[RawCell]]) -> tuple[GridCells, list[Merge]]:
    """按 occupancy 算法把带 ``rowspan/colspan`` 的行展开成网格 + 合并区。

    返回 ``(cells, merges)``：``cells[(r, c)] = text``（0 基），
    ``merges`` 为 ``(r1, c1, r2, c2)`` 列表（仅跨行 / 跨列的单元格）。
    """
    cells: GridCells = {}
    merges: list[Merge] = []
    occupied: set[tuple[int, int]] = set()
    for r, row in enumerate(rows):
        c = 0
        for text, rowspan, colspan in row:
            while (r, c) in occupied:
                c += 1
            cells[(r, c)] = text
            for dr in range(rowspan):
                for dc in range(colspan):
                    occupied.add((r + dr, c + dc))
            if rowspan > 1 or colspan > 1:
                merges.append((r, c, r + rowspan - 1, c + colspan - 1))
            c += colspan
    return cells, merges


def grid_dimensions(cells: GridCells, merges: list[Merge]) -> tuple[int, int]:
    """由 ``cells``/``merges`` 推出网格行列数（建 pptx table 需先知尺寸）。"""
    max_r = max((rc[0] for rc in cells), default=-1)
    max_c = max((rc[1] for rc in cells), default=-1)
    for merge in merges:
        max_r = max(max_r, merge[2])
        max_c = max(max_c, merge[3])
    return max_r + 1, max_c + 1
