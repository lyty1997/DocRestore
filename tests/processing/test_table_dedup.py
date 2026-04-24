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

"""HTML 表格去重测试。"""

from __future__ import annotations

from docrestore.processing.table_dedup import (
    _table_signature,
    dedup_html_tables,
)


def _table(*rows: list[str]) -> str:
    """快捷构造 <table>。每行是 cell 列表。"""
    tr_blocks = []
    for cells in rows:
        td = "".join(f"<td>{c}</td>" for c in cells)
        tr_blocks.append(f"<tr>{td}</tr>")
    return f"<table border=1>{''.join(tr_blocks)}</table>"


class TestSignature:
    def test_signature_strips_html_and_whitespace(self) -> None:
        t = "<table><tr><td>  A  </td><td>B</td></tr></table>"
        assert _table_signature(t) == "A | B"

    def test_signature_ignores_attributes(self) -> None:
        t1 = "<table border=1><tr><td>X</td></tr></table>"
        t2 = '<table border=1 style="x"><tr style="y"><td>X</td></tr></table>'
        assert _table_signature(t1) == _table_signature(t2)


class TestDedupBasic:
    def test_no_tables_returns_unchanged(self) -> None:
        md = "## A\n正文\n## B"
        out, removed = dedup_html_tables(md)
        assert out == md
        assert removed == []

    def test_single_table_no_dedup(self) -> None:
        md = f"## A\n{_table(['x', 'y'], ['a', 'b'])}\n## B"
        out, removed = dedup_html_tables(md)
        assert out == md
        assert removed == []

    def test_two_identical_tables_keeps_one(self) -> None:
        t = _table(
            ["类", "配置", "功能"],
            ["设备", "GPIO", "支持"],
            ["设备", "I2C", "支持"],
            ["设备", "MTD", "支持"],
        )
        md = f"前文\n{t}\n中间正文\n{t}\n后文"
        out, removed = dedup_html_tables(md)
        assert out.count("<table") == 1
        assert len(removed) == 1
        assert removed[0]["kept_cells"] == removed[0]["removed_cells"]
        assert "中间正文" in out
        assert "前文" in out
        assert "后文" in out

    def test_keeps_longer_version(self) -> None:
        """两表 sig 高度相似但一个更完整 → 保留长的。"""
        short = _table(["类", "配置"], ["A", "1"], ["B", "2"])
        long = _table(
            ["类", "配置"],
            ["A", "1"], ["B", "2"], ["C", "3"], ["D", "4"], ["E", "5"],
        )
        md = f"前\n{short}\n中\n{long}\n后"
        out, removed = dedup_html_tables(md, sim_threshold=0.5)
        assert out.count("<table") == 1
        # 应保留 long
        assert "E" in out
        kept_cells = removed[0]["kept_cells"]
        removed_cells = removed[0]["removed_cells"]
        assert isinstance(kept_cells, int)
        assert isinstance(removed_cells, int)
        assert kept_cells > removed_cells

    def test_three_identical_keeps_one(self) -> None:
        """三个完全相同 → 删两个，保留一个（首现）。"""
        t = _table(
            ["x", "y", "z"], ["a", "b", "c"],
            ["d", "e", "f"], ["g", "h", "i"],
        )
        md = f"前\n{t}\n中1\n{t}\n中2\n{t}\n后"
        out, removed = dedup_html_tables(md)
        assert out.count("<table") == 1
        assert len(removed) == 2

    def test_different_tables_kept(self) -> None:
        """两个内容明显不同的表都应保留。"""
        t1 = _table(
            ["命令", "说明"], ["ls", "list"], ["cd", "change"],
            ["rm", "remove"], ["cp", "copy"],
        )
        t2 = _table(
            ["寄存器", "地址"], ["UART", "0x100"], ["GPIO", "0x200"],
            ["I2C", "0x300"], ["SPI", "0x400"],
        )
        md = f"前\n{t1}\n中\n{t2}\n后"
        out, removed = dedup_html_tables(md)
        assert out.count("<table") == 2
        assert removed == []

    def test_short_tables_skipped(self) -> None:
        """单元格数 < min_cells 的小表跳过去重（误判风险高）。"""
        tiny = "<table><tr><td>A</td></tr></table>"
        md = f"a\n{tiny}\nb\n{tiny}\nc"
        out, removed = dedup_html_tables(md, min_cells=3)
        # 都保留
        assert out.count("<table") == 2
        assert removed == []


class TestSpacingAfterRemoval:
    def test_paragraph_separator_preserved(self) -> None:
        """删除一张表后前后段落保留 1 个空行分隔。"""
        t = _table(["x", "y"], ["a", "b"], ["c", "d"], ["e", "f"])
        md = f"段落 A\n\n{t}\n\n段落 B\n\n{t}\n\n段落 C"
        out, _ = dedup_html_tables(md)
        # 两段相邻不能粘连
        assert "段落 BC" not in out
        assert "段落 B" in out
        assert "段落 C" in out
        # 没有 3 个连续空行
        assert "\n\n\n\n" not in out

    def test_no_orphan_blank_lines(self) -> None:
        """删除后不产生连续 ≥3 空行。"""
        t = _table(["a", "b"], ["c", "d"], ["e", "f"], ["g", "h"])
        md = f"前\n\n{t}\n\n{t}\n\n后"
        out, _ = dedup_html_tables(md)
        assert "\n\n\n\n" not in out


class TestRealWorldUBootCase:
    def test_uboot_repeated_config_table(self) -> None:
        """复现 U-Boot 实测：4 份 23 行的"类|配置|功能"表连续重复。"""
        rows = [["类", "配置", "功能"]]
        rows.append(["设备管理", "CONFIG_DM_GPIO", "支持GPIO设备模型"])
        rows.append(["设备管理", "CONFIG_DM_I2C", "支持I2C总线设备模型"])
        rows.append(["设备管理", "CONFIG_DM_MTD", "支持MTD设备模型"])
        rows.append(["设备管理", "CONFIG_DM_VIDEO", "支持视频设备模型"])
        rows.append(["设备管理", "CONFIG_DM_SPI", "支持SPI设备模型"])
        big_table = _table(*rows)
        # 4 份完全相同 + 中间穿插少量正文
        md = (
            "## 配置说明\n"
            f"{big_table}\n\n"
            "正文一\n\n"
            f"{big_table}\n\n"
            "正文二\n\n"
            f"{big_table}\n\n"
            "正文三\n\n"
            f"{big_table}\n\n"
            "## 后续\n"
        )
        out, removed = dedup_html_tables(md)
        assert out.count("<table") == 1
        assert len(removed) == 3
        # 正文不应被吃
        assert "正文一" in out
        assert "正文二" in out
        assert "正文三" in out
        # 标题保留
        assert "## 配置说明" in out
        assert "## 后续" in out
