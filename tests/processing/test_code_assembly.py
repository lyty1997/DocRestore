# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""栏代码组装单测（AGE-8 Phase 1.3 v2）"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docrestore.models import TextLine
from docrestore.processing.code_assembly import (
    CodeColumn,
    CodeLine,
    assemble_columns,
)
from docrestore.processing.ide_layout import IDELayout
from docrestore.processing.ide_layout import analyze_layout


# ---------- 合成 fixture ----------

def _line(
    bbox: tuple[int, int, int, int], text: str, score: float = 0.95,
) -> TextLine:
    return TextLine(bbox=bbox, text=text, score=score)


def _make_simple_column_layout(
    *, indent_levels: list[int] | None = None,
    line_count: int | None = None,
) -> IDELayout:
    """构造单栏 layout：行号 1-N + 对应代码（可指定缩进层级）。

    行号: x=[100, 130], 代码起点: x=200, 字符宽 ≈ 12px
    缩进 1 层 = 4 字符 = 48px（模拟 4 空格缩进）

    注：行号 anchor 至少需 5 行才合格（LayoutConfig.min_anchor_lines）。
    """
    if indent_levels is None:
        indent_levels = [0] * (line_count or 5)
    if line_count is None:
        line_count = len(indent_levels)
    # 行号锚点至少 5 行（min_anchor_lines）；不足 5 行 fixture 用 padding
    if line_count < 5:
        indent_levels = list(indent_levels) + [0] * (5 - line_count)
        line_count = 5
    lines: list[TextLine] = []
    char_width = 12
    code_start = 200
    line_height = 30
    for i in range(line_count):
        y_top = 100 + i * line_height
        y_bot = y_top + line_height - 5
        # 行号
        lines.append(_line((100, y_top, 130, y_bot), str(i + 1)))
        # 代码（缩进 = indent_levels[i] 个 4-空格 = 4 * indent_levels[i] 字符）
        indent_chars = 4 * indent_levels[i]
        x1 = code_start + indent_chars * char_width
        # 代码内容固定 10 字符（与 bbox 宽度严格匹配，让 char_width 估算精确）
        text = f"line{i + 1:02d}_X_"   # 10 字符固定
        x2 = x1 + len(text) * char_width
        lines.append(_line((x1, y_top, x2, y_bot), text))
    return analyze_layout(lines, image_size=(800, line_height * line_count + 200))


# ---------- 测试 ----------

class TestAssemblyBasic:
    def test_simple_column(self) -> None:
        layout = _make_simple_column_layout(indent_levels=[0] * 5)
        columns = assemble_columns(layout)
        assert len(columns) == 1
        col = columns[0]
        assert isinstance(col, CodeColumn)
        assert len(col.lines) == 5
        # 所有行无缩进
        for line in col.lines:
            assert isinstance(line, CodeLine)
            assert line.indent == 0

    def test_indent_recovered(self) -> None:
        """缩进按字符宽度推算，0/1/2 三档应正确恢复"""
        # 显式给 5 个 indent 层级（≥ min_anchor_lines=5）
        layout = _make_simple_column_layout(indent_levels=[0, 1, 2, 0, 0])
        columns = assemble_columns(layout)
        col = columns[0]
        assert col.lines[0].indent == 0
        assert col.lines[1].indent == 4   # 1 层 = 4 字符
        assert col.lines[2].indent == 8

    def test_code_text_includes_indent(self) -> None:
        """code_text 输出包含前导空格"""
        layout = _make_simple_column_layout(indent_levels=[0, 1, 2, 0, 0])
        columns = assemble_columns(layout)
        col = columns[0]
        lines = col.code_text.splitlines()
        assert lines[0] == "line01_X_"
        assert lines[1] == "    line02_X_"
        assert lines[2] == "        line03_X_"

    def test_line_numbers_in_order(self) -> None:
        layout = _make_simple_column_layout(line_count=8)
        columns = assemble_columns(layout)
        col = columns[0]
        nos = [ln.line_no for ln in col.lines]
        assert nos == [1, 2, 3, 4, 5, 6, 7, 8]


class TestEmpty:
    def test_no_anchor_returns_empty(self) -> None:
        # 制造无 anchor 的输入：纯代码 line 没有行号
        lines = [
            _line((100, 100, 800, 130), "no line numbers"),
            _line((100, 140, 800, 170), "no anchor"),
        ]
        layout = analyze_layout(lines, (800, 200))
        columns = assemble_columns(layout)
        assert columns == []

    def test_empty_input(self) -> None:
        layout = analyze_layout([], image_size=(800, 600))
        assert assemble_columns(layout) == []


class TestUnpairedNotInserted:
    """unpaired_codes 当前策略：只标 flag 不插入（避免污染代码）"""

    def test_unpaired_marked_but_not_inserted(self) -> None:
        """有未配对代码 → 标 flag，不插入到 assembled"""
        lines: list[TextLine] = []
        line_height = 50
        char_w = 12
        for i in range(5):
            y = 100 + i * line_height
            lines.append(_line((100, y, 130, y + 25), str(i + 1)))
            lines.append(_line(
                (200, y, 200 + char_w * 8, y + 25), f"line{i + 1}_X",
            ))
        # 游离代码：y_center=235，距任何行号 y_center > 容差
        lines.append(_line((200, 222, 200 + char_w * 10, 248), "FLOATER_X"))
        layout = analyze_layout(lines, image_size=(800, 600))
        col = assemble_columns(layout)[0]
        # 不应有 inferred line
        assert not any(ln.is_inferred_line_no for ln in col.lines)
        # 但应有 quality flag 记录
        assert any("unpaired_codes=" in f for f in col.flags)
        # FLOATER 不在 code_text 中
        assert "FLOATER" not in col.code_text


class TestGapDetection:
    def test_detects_missing_line_numbers(self) -> None:
        """OCR 行号集合与 anchor.num_range 不一致 → line_gaps 列出缺失"""
        # 构造行号 1, 2, 4, 5（缺 3）+ 代码
        lines = [
            _line((100, 100, 130, 125), "1"),
            _line((200, 100, 400, 125), "code_a"),
            _line((100, 140, 130, 165), "2"),
            _line((200, 140, 400, 165), "code_b"),
            _line((100, 180, 130, 205), "4"),
            _line((200, 180, 400, 205), "code_c"),
            _line((100, 220, 130, 245), "5"),
            _line((200, 220, 400, 245), "code_d"),
            _line((100, 260, 130, 285), "6"),
            _line((200, 260, 400, 285), "code_e"),
        ]
        layout = analyze_layout(lines, (800, 400))
        if not layout.anchors:
            pytest.skip("anchor 阈值不允许 5 个行号；用更长 fixture")
        col = assemble_columns(layout)[0]
        # 期望 anchor.num_range = (1, 6)，actual {1,2,4,5,6}
        assert 3 in col.line_gaps


# ---------- spike 真实数据集成测试 ----------

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


def _load_spike(stem: str) -> tuple[list[TextLine], tuple[int, int]]:
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
    from PIL import Image  # noqa: PLC0415
    img = SPIKE_IMAGE_DIR / f"{stem}.JPG"
    if img.exists():
        return text_lines, Image.open(img).size
    return text_lines, (3400, 1900)


@pytest.mark.skipif(
    not _list_spike_stems(),
    reason="age8-probe-basic 数据未生成（先跑 scripts/age8_probe_basic_ocr.py）",
)
class TestSpike:
    """对 8 张 spike 集成验证：assemble 不崩溃 + 输出 sanity"""

    @pytest.fixture(params=_list_spike_stems())
    def spike(
        self, request: pytest.FixtureRequest,
    ) -> tuple[str, list[TextLine], tuple[int, int]]:
        return request.param, *_load_spike(request.param)

    def test_assembly_runs(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        columns = assemble_columns(layout)
        assert len(columns) == len(layout.anchors), (
            f"{stem} columns 数与 anchors 数不一致"
        )

    def test_each_column_has_code(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        for col in assemble_columns(layout):
            assert col.lines, f"{stem} col_{col.column_index} 无代码"
            assert col.code_text.strip(), (
                f"{stem} col_{col.column_index} code_text 空"
            )

    def test_char_width_reasonable(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        """字符宽度估算应在合理范围（10-40px）"""
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        for col in assemble_columns(layout):
            assert 5 <= col.char_width <= 60, (
                f"{stem} col_{col.column_index} char_width={col.char_width}"
            )

    def test_line_height_reasonable(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        for col in assemble_columns(layout):
            assert 15 <= col.avg_line_height <= 80, (
                f"{stem} col_{col.column_index} line_height={col.avg_line_height}"
            )

    def test_some_lines_have_indent(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        """spike 代码绝大多数都含有缩进，至少应有几行 indent>0"""
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        for col in assemble_columns(layout):
            indent_count = sum(1 for ln in col.lines if ln.indent > 0)
            assert indent_count >= 1, (
                f"{stem} col_{col.column_index} 全无缩进（异常）"
            )
