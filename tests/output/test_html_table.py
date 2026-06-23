# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""导出公共件 ``html_table`` 测试（xlsx / pptx 共用的 HTML 表解析层）。

纯函数、无第三方依赖：``parse_tables`` / ``parse_one_table`` / ``build_grid`` /
``grid_dimensions``，断言从构造输入派生（不写死数据集关键词）。
"""

from __future__ import annotations

from docrestore.output.exporters.html_table import (
    build_grid,
    grid_dimensions,
    parse_one_table,
    parse_tables,
)


def test_parse_tables_multiple() -> None:
    """两个 ``<table>`` → 两张表；正文文本不混入。"""
    md = (
        "<table><tr><td>甲</td></tr></table>\n\n正文\n\n"
        "<table><tr><td>乙</td></tr></table>"
    )
    tables = parse_tables(md)
    assert len(tables) == 2
    assert tables[0][0][0][0] == "甲"
    assert tables[1][0][0][0] == "乙"


def test_parse_tables_drops_empty() -> None:
    """无 ``<table>`` → 空列表。"""
    assert parse_tables("# 标题\n\n纯正文无表。") == []


def test_parse_one_table_cells() -> None:
    """单表片段 → 行/单元格文本（含跨列属性）。"""
    rows = parse_one_table(
        '<table><tr><td colspan="2">合并头</td></tr>'
        "<tr><td>左</td><td>右</td></tr></table>",
    )
    assert rows[0][0] == ("合并头", 1, 2)  # (文本, rowspan, colspan)
    assert [c[0] for c in rows[1]] == ["左", "右"]


def test_build_grid_merge_and_dimensions() -> None:
    """occupancy 展开：跨列头 + 两列 → 1 个合并区，网格 2×2。"""
    rows = parse_one_table(
        '<table><tr><td colspan="2">头</td></tr>'
        "<tr><td>a</td><td>b</td></tr></table>",
    )
    cells, merges = build_grid(rows)
    assert cells[(0, 0)] == "头"
    assert cells[(1, 0)] == "a"
    assert cells[(1, 1)] == "b"
    assert merges == [(0, 0, 0, 1)]  # 第一行跨两列
    assert grid_dimensions(cells, merges) == (2, 2)


def test_grid_dimensions_rowspan() -> None:
    """跨行单元格也计入网格尺寸。"""
    rows = parse_one_table(
        '<table><tr><td rowspan="2">竖</td><td>x</td></tr>'
        "<tr><td>y</td></tr></table>",
    )
    cells, merges = build_grid(rows)
    assert grid_dimensions(cells, merges) == (2, 2)
    assert merges == [(0, 0, 1, 0)]  # 第一列跨两行
