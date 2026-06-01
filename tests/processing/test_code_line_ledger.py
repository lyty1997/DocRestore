# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stage 0 行账本完整性校验单测（AGE-79）

覆盖：干净列 / 行号非递增（误识）/ 重复行号 / inferred 行 / 回查原图 OCR
配对存疑 / 空行 / 置信度回填 / trustable_anchors 访问器 / 空列。

测试数据全部用通用占位代码，不写死任何数据集标识符（CLAUDE.md 测试规则）。
"""

from __future__ import annotations

import pytest

from docrestore.models import TextLine
from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_line_ledger import (
    LedgerConfig,
    LineLedger,
    build_line_ledger,
)

_CHAR_W = 10
_HEIGHT = 28


def _cline(
    line_no: int,
    text: str,
    y_top: int,
    *,
    x1: int = 200,
    indent: int = 0,
    inferred: bool = False,
    has_bbox: bool = True,
) -> CodeLine:
    """构造一行 CodeLine；bbox 宽度按文本长度估算。"""
    bbox: tuple[int, int, int, int] | None = None
    if has_bbox:
        x2 = x1 + max(1, len(text)) * _CHAR_W
        bbox = (x1, y_top, x2, y_top + _HEIGHT)
    return CodeLine(
        line_no=line_no, text=text, indent=indent,
        bbox=bbox, is_inferred_line_no=inferred,
    )


def _column(lines: list[CodeLine], *, idx: int = 0) -> CodeColumn:
    boxes = [ln.bbox for ln in lines if ln.bbox is not None]
    if boxes:
        bbox = (
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        )
    else:
        bbox = (0, 0, 0, 0)
    return CodeColumn(
        column_index=idx,
        bbox=bbox,
        code_text="\n".join(ln.text for ln in lines),
        lines=lines,
        char_width=float(_CHAR_W),
        avg_line_height=_HEIGHT,
    )


def _ocr_mirror(
    lines: list[CodeLine],
    *,
    score: float = 0.95,
    text_overrides: dict[int, str] | None = None,
) -> list[TextLine]:
    """从 CodeLine 反向造原图 OCR text_lines：同 bbox，文本默认相同。

    ``text_overrides`` 按 line_no 覆盖某行的 OCR 文本，用于模拟错配。
    """
    overrides = text_overrides or {}
    out: list[TextLine] = []
    for ln in lines:
        if ln.bbox is None:
            continue
        out.append(TextLine(
            bbox=ln.bbox,
            text=overrides.get(ln.line_no, ln.text),
            score=score,
        ))
    return out


def test_clean_column_all_trustable() -> None:
    """干净列：行号递增、文本忠实 → 全部可作锚点，无 flag。"""
    lines = [
        _cline(10, "int a = 0;", 100),
        _cline(11, "int b = 1;", 130),
        _cline(12, "return a + b;", 160),
    ]
    ledger = build_line_ledger("p1", _column(lines), _ocr_mirror(lines))

    assert ledger.page_stem == "p1"
    assert ledger.column_index == 0
    assert set(ledger.entries) == {10, 11, 12}
    assert all(e.anchor_trustable for e in ledger.entries.values())
    assert ledger.flags == []
    assert ledger.trustable_anchors().keys() == {10, 11, 12}


def test_nonmonotonic_line_number_flagged() -> None:
    """视觉 y 递增但行号 10→8→12（8 是误识）→ 行 8 不可信，标 nonmonotonic。"""
    lines = [
        _cline(10, "foo();", 100),
        _cline(8, "bar();", 130),   # y 在 10 之下，行号却更小 → 违例
        _cline(12, "baz();", 160),
    ]
    ledger = build_line_ledger("p1", _column(lines), _ocr_mirror(lines))

    assert ledger.entries[8].anchor_trustable is False
    assert ledger.entries[10].anchor_trustable is True
    assert ledger.entries[12].anchor_trustable is True
    assert "code.line.nonmonotonic=1" in ledger.flags
    assert 8 not in ledger.trustable_anchors()


def test_duplicate_line_number_flagged() -> None:
    """同一栏出现两个行号 10 → 该行号不可信，标 nonmonotonic。"""
    lines = [
        _cline(10, "first();", 100),
        _cline(10, "dup();", 130),
        _cline(11, "ok();", 160),
    ]
    ledger = build_line_ledger("p1", _column(lines), _ocr_mirror(lines))

    assert ledger.entries[10].anchor_trustable is False
    assert ledger.entries[11].anchor_trustable is True
    assert any(f.startswith("code.line.nonmonotonic=") for f in ledger.flags)


def test_inferred_line_not_anchor() -> None:
    """is_inferred_line_no=True 的行不可作锚点，标 inferred。"""
    lines = [
        _cline(10, "a();", 100),
        _cline(11, "b();", 130, inferred=True),
        _cline(12, "c();", 160),
    ]
    ledger = build_line_ledger("p1", _column(lines), _ocr_mirror(lines))

    assert ledger.entries[11].anchor_trustable is False
    assert "code.line.inferred=1" in ledger.flags


def test_pairing_suspect_when_text_mismatches_ocr() -> None:
    """CodeLine.text 与 bbox 内原图 OCR 文本对不上 → 配对存疑，不可信。"""
    lines = [
        _cline(10, "int total = sum(values);", 100),
        _cline(11, "ok();", 130),
    ]
    ocr = _ocr_mirror(
        lines, text_overrides={10: "completely unrelated text here"},
    )
    ledger = build_line_ledger("p1", _column(lines), ocr)

    assert ledger.entries[10].anchor_trustable is False
    assert ledger.entries[11].anchor_trustable is True
    assert "code.line.pairing_suspect=1" in ledger.flags


def test_confidence_backfilled_from_ocr_score() -> None:
    """置信度回填：忠实行的 confidence ≈ 命中 text_lines 的平均 OCR score。"""
    lines = [_cline(10, "x();", 100), _cline(11, "y();", 130)]
    ledger = build_line_ledger(
        "p1", _column(lines), _ocr_mirror(lines, score=0.6),
    )
    assert ledger.entries[10].confidence == pytest.approx(0.6, abs=1e-3)


def test_empty_line_not_anchor_but_not_flagged() -> None:
    """空行（bbox=None / 空文本）不可作锚点，但不算 nonmonotonic/suspect。"""
    lines = [
        _cline(10, "a();", 100),
        _cline(11, "", 0, has_bbox=False),
        _cline(12, "c();", 160),
    ]
    ledger = build_line_ledger("p1", _column(lines), _ocr_mirror(lines))

    assert ledger.entries[11].anchor_trustable is False
    assert ledger.entries[11].confidence == 0.0
    assert ledger.flags == []  # 空行不污染 flag


def test_empty_column_returns_empty_ledger() -> None:
    """column.lines 为空 → 返回 entries 为空的 ledger（不抛）。"""
    ledger = build_line_ledger("p1", _column([]), [])
    assert isinstance(ledger, LineLedger)
    assert ledger.entries == {}
    assert ledger.flags == []


def test_faithful_threshold_configurable() -> None:
    """调高 faithful_min_ratio 可让轻微差异也判存疑（阈值可配验证）。"""
    lines = [_cline(10, "value = compute(x)", 100)]
    ocr = _ocr_mirror(lines, text_overrides={10: "value = compute(y)"})
    strict = LedgerConfig(faithful_min_ratio=0.99)
    ledger = build_line_ledger("p1", _column(lines), ocr, strict)
    assert ledger.entries[10].anchor_trustable is False
