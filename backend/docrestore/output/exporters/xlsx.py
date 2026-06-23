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

"""xlsx 导出器（D4）：``document.md`` → Excel，走 openpyxl。

`document.md` 里的表格**一律是 HTML ``<table>``**（LLM prompt 默认保留 HTML 原样，
`table_dedup.py` 也按 ``<tr>/<td>`` 去重）。HTML 表本身携带行列与 ``colspan/rowspan``——
它就是结构化 IR，无需 pipeline 旁路。本导出器在**下载环节**解析每个 ``<table>`` →
单元格矩阵 + 合并区，openpyxl **每表一 sheet**；纯数字单元格转数值；合并区
``merge_cells``。文档无表 → 退化为单 ``Document`` sheet（每非空 markdown 行一行，
产物非空便于派生断言）。

openpyxl 是纯 Python 依赖（无系统库）：**惰性导入** fail-closed
（:class:`ExportToolUnavailable`）——注册表启动时导入本模块，顶层不 import openpyxl。
详见 ``docs/zh/export-mode.md`` §9.1。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TypeAlias

from docrestore.output.exporters.base import (
    ExportFailed,
    ExportToolUnavailable,
)

#: 匹配一个 HTML 表格块（与 ``table_dedup.py`` 同口径）
_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
#: 纯整数（禁前导 0，避免电话/编号被转成数字丢前导 0）
_INT_RE = re.compile(r"-?[1-9]\d*|0")
#: 纯小数
_FLOAT_RE = re.compile(r"-?\d+\.\d+")
#: 退化文本 sheet 的最大行数（挡超大文档失控写盘）
_MAX_TEXT_ROWS = 5000

#: 单元格值的联合类型（openpyxl 接受 str/int/float）
CellValue: TypeAlias = str | int | float
#: 解析出的一行单元格：(文本, rowspan, colspan)
RawCell: TypeAlias = tuple[str, int, int]


def _clean_text(raw: str) -> str:
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
                    (_clean_text("".join(self._buf)), rowspan, colspan),
                )
            self._in_cell = False
        elif name == "tr" and self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None


def _parse_tables(markdown: str) -> list[list[list[RawCell]]]:
    """抽取 markdown 中所有 ``<table>`` → 各表的 ``rows``（丢弃空表）。"""
    tables: list[list[list[RawCell]]] = []
    for match in _TABLE_RE.finditer(markdown):
        parser = _TableHTMLParser()
        parser.feed(match.group(0))
        parser.close()
        non_empty = [row for row in parser.rows if row]
        if non_empty:
            tables.append(non_empty)
    return tables


def _build_grid(
    rows: list[list[RawCell]],
) -> tuple[dict[tuple[int, int], str], list[tuple[int, int, int, int]]]:
    """按 occupancy 算法把带 ``rowspan/colspan`` 的行展开成网格 + 合并区。

    返回 ``(cells, merges)``：``cells[(r, c)] = text``（0 基），
    ``merges`` 为 ``(r1, c1, r2, c2)`` 列表（仅跨行 / 跨列的单元格）。
    """
    cells: dict[tuple[int, int], str] = {}
    merges: list[tuple[int, int, int, int]] = []
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


def _coerce_cell(text: str) -> CellValue:
    """纯数字单元格 → 数值（``"100"``→100），否则保留字符串。"""
    if _INT_RE.fullmatch(text):
        return int(text)
    if _FLOAT_RE.fullmatch(text):
        return float(text)
    return text


class XlsxExporter:
    """``document.md`` → xlsx（openpyxl，每表一 sheet）。"""

    suffix = "xlsx"
    tool = "openpyxl"

    def ensure_available(self) -> None:
        """openpyxl 不可导入 → fail-closed。"""
        try:
            import openpyxl  # noqa: F401, PLC0415 — 惰性导入仅做可用性探测
        except ImportError as exc:
            raise ExportToolUnavailable(self.tool) from exc

    def export(
        self,
        doc_md: Path,
        assets_dir: Path,  # noqa: ARG002 — xlsx 不嵌图，仅消费表格文本
        out_path: Path,
    ) -> None:
        """解析 ``doc_md`` 的 HTML 表 → openpyxl 工作簿；无表则落正文 sheet。"""
        try:
            import openpyxl  # noqa: PLC0415 — 惰性导入，避免缺依赖时整体起不来
        except ImportError as exc:
            raise ExportToolUnavailable(self.tool) from exc

        markdown = doc_md.read_text(encoding="utf-8")
        tables = _parse_tables(markdown)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            workbook = openpyxl.Workbook()
            default_sheet = workbook.active  # 去掉默认空 sheet
            if default_sheet is not None:
                workbook.remove(default_sheet)
            if tables:
                for idx, rows in enumerate(tables, 1):
                    sheet = workbook.create_sheet(f"Table {idx}")
                    _fill_table_sheet(sheet, rows)
            else:
                _fill_text_sheet(workbook.create_sheet("Document"), markdown)
            workbook.save(str(out_path))
        except Exception as exc:  # noqa: BLE001 — openpyxl 异常统一 fail-closed
            raise ExportFailed(self.tool, self.suffix, str(exc)[:500]) from exc


def _fill_table_sheet(sheet: object, rows: list[list[RawCell]]) -> None:
    """把一张表写入 openpyxl sheet（含数值转换与合并区）。"""
    cells, merges = _build_grid(rows)
    for (r, c), text in cells.items():
        sheet.cell(row=r + 1, column=c + 1, value=_coerce_cell(text))  # type: ignore[attr-defined]
    for r1, c1, r2, c2 in merges:
        sheet.merge_cells(  # type: ignore[attr-defined]
            start_row=r1 + 1, start_column=c1 + 1,
            end_row=r2 + 1, end_column=c2 + 1,
        )


def _fill_text_sheet(sheet: object, markdown: str) -> None:
    """无表退化：每非空 markdown 行写入 A 列一行（产物非空）。"""
    row = 1
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        sheet.cell(row=row, column=1, value=stripped)  # type: ignore[attr-defined]
        row += 1
        if row > _MAX_TEXT_ROWS:
            break
