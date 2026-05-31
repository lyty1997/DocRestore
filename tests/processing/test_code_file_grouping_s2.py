# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""S2 行号锚定跨页归类单测（AGE-81）

覆盖：重合裁决三分支（confirm / conflict / weak / insufficient）、非可信行排除、
跨桶救援命中、orphan 无重合不救援、内容冲突不救援、组内 overlap_confirmed 标注、
无 ledger 向后兼容。

测试数据用通用占位内容，不写死数据集标识符（CLAUDE.md 测试规则）。
"""

from __future__ import annotations

from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import (
    GroupingConfig,
    PageColumn,
    _overlap_verdict,
    group_into_files,
)
from docrestore.processing.code_line_ledger import LineEntry, LineLedger
from docrestore.processing.ide_meta_extract import IDEMeta


def _seg(lo: int, hi: int) -> dict[int, str]:
    """一份文件的逐行内容（行号 -> 该行文本）。"""
    return {n: f"value_{n} = compute_sum({n})" for n in range(lo, hi + 1)}


def _alt(lo: int, hi: int) -> dict[int, str]:
    """另一份文件的逐行内容：与 ``_seg`` 同行号但内容截然不同。"""
    return {n: f"render_widget[{n}].draw_frame()" for n in range(lo, hi + 1)}


def _entries(
    line_texts: dict[int, str], *, trustable: bool = True,
) -> dict[int, LineEntry]:
    return {
        n: LineEntry(line_no=n, text=t, indent=0,
                     anchor_trustable=trustable, confidence=0.9)
        for n, t in line_texts.items()
    }


def _pc(stem: str, col: int, path: str, line_texts: dict[int, str]) -> PageColumn:
    filename = path.rsplit("/", 1)[-1]
    meta = IDEMeta(
        column_index=col, filename=filename, path=path,
        language="cpp", path_confidence=0.9,
    )
    ordered = sorted(line_texts.items())
    lines = [CodeLine(line_no=n, text=t, indent=0, bbox=None) for n, t in ordered]
    column = CodeColumn(
        column_index=col, bbox=(0, 0, 0, 0),
        code_text="\n".join(t for _, t in ordered),
        lines=lines, char_width=1.0, avg_line_height=1,
    )
    return PageColumn(page_stem=stem, column_index=col, meta=meta, column=column)


def _page(
    pcs: list[PageColumn],
    ledgers: dict[tuple[str, int], LineLedger],
    stem: str, col: int, path: str, line_texts: dict[int, str],
) -> None:
    pcs.append(_pc(stem, col, path, line_texts))
    ledgers[(stem, col)] = LineLedger(
        page_stem=stem, column_index=col, entries=_entries(line_texts),
    )


def _run_pages(
    pcs: list[PageColumn], ledgers: dict[tuple[str, int], LineLedger], path: str,
) -> None:
    """5 页一个真实 run，相邻页行号重合且内容一致。"""
    for i, (lo, hi) in enumerate([(1, 10), (8, 18), (16, 26), (24, 34), (32, 42)]):
        _page(pcs, ledgers, f"r{i}", 0, path, _seg(lo, hi))


# ---------- _overlap_verdict 三分支 ----------

def test_verdict_confirm() -> None:
    a = _entries(_seg(10, 16))
    b = _entries(_seg(10, 16))
    verdict, ratio, n = _overlap_verdict(a, b, GroupingConfig())
    assert verdict == "confirm"
    assert ratio == 1.0
    assert n == 7


def test_verdict_conflict() -> None:
    a = _entries(_seg(10, 16))
    b = _entries(_alt(10, 16))
    verdict, _ratio, _n = _overlap_verdict(a, b, GroupingConfig())
    assert verdict == "conflict"


def test_verdict_weak() -> None:
    a = _entries({n: f"L{n}" for n in range(1, 6)})
    b = _entries({1: "L1", 2: "L2", 3: "L3", 4: "ZZZ", 5: "QQQ"})  # 3/5 match
    verdict, ratio, _n = _overlap_verdict(a, b, GroupingConfig())
    assert verdict == "weak"
    assert 0.5 < ratio < 0.9


def test_verdict_insufficient_overlap() -> None:
    a = _entries(_seg(1, 2))
    b = _entries(_seg(1, 2))
    verdict, _ratio, n = _overlap_verdict(a, b, GroupingConfig())
    assert verdict == "insufficient"  # 仅 2 < overlap_min_lines(3)
    assert n == 2


def test_verdict_excludes_untrustable_lines() -> None:
    """非 anchor_trustable 的行不计入共享集。"""
    a = _entries(_seg(10, 16), trustable=False)
    b = _entries(_seg(10, 16))
    verdict, _ratio, n = _overlap_verdict(a, b, GroupingConfig())
    assert verdict == "insufficient"
    assert n == 0


# ---------- 跨桶救援 ----------

def test_cross_bucket_rescue_merges_garbage_fragment() -> None:
    """garbage 文件名的孤页，行号重合区内容一致 → 归并进真实 run，名取 run 的。"""
    pcs: list[PageColumn] = []
    ledgers: dict[tuple[str, int], LineLedger] = {}
    _run_pages(pcs, ledgers, "a/b/real.cc")
    _page(pcs, ledgers, "orphan", 0, "x/giesz.cc", _seg(20, 28))  # 与 r2(16-26) 重合
    sources = group_into_files(pcs, ledgers, GroupingConfig())
    assert len(sources) == 1
    only = sources[0]
    assert only.path == "a/b/real.cc"
    assert len(only.pages) == 6
    assert "code.group.cross_bucket_rescued" in only.flags


def test_orphan_without_overlap_not_rescued() -> None:
    """孤页行号与任何 run 都不相交 → 不救援，标 orphan_unrescued，独立成文件。"""
    pcs: list[PageColumn] = []
    ledgers: dict[tuple[str, int], LineLedger] = {}
    _run_pages(pcs, ledgers, "a/b/real.cc")
    _page(pcs, ledgers, "orphan", 0, "x/giesz.cc", _seg(100, 108))  # 无重合
    sources = group_into_files(pcs, ledgers, GroupingConfig())
    assert len(sources) == 2
    orphan = next(s for s in sources if s.filename == "giesz.cc")
    assert "code.group.orphan_unrescued" in orphan.flags


def test_content_conflict_blocks_rescue() -> None:
    """孤页行号与 run 重合但内容冲突 → 不救援（宁可漏救不可错并）。"""
    pcs: list[PageColumn] = []
    ledgers: dict[tuple[str, int], LineLedger] = {}
    _run_pages(pcs, ledgers, "a/b/real.cc")
    _page(pcs, ledgers, "orphan", 0, "x/other.cc", _alt(20, 28))
    sources = group_into_files(pcs, ledgers, GroupingConfig())
    assert len(sources) == 2  # 未并
    assert any(s.filename == "other.cc" for s in sources)


def test_weak_overlap_with_gap_bridging_rescues() -> None:
    """weak 重合（多数行一致非冲突）+ 填补 run 行号缺口 → 救援（结构桥接）。"""
    pcs: list[PageColumn] = []
    ledgers: dict[tuple[str, int], LineLedger] = {}
    # run 覆盖 1-18 与 30-48，缺口 19-29
    _page(pcs, ledgers, "r0", 0, "a/b/real.cc", _seg(1, 10))
    _page(pcs, ledgers, "r1", 0, "a/b/real.cc", _seg(8, 18))
    _page(pcs, ledgers, "r2", 0, "a/b/real.cc", _seg(30, 40))
    _page(pcs, ledgers, "r3", 0, "a/b/real.cc", _seg(38, 48))
    # orphan 14-29：与 r1 在 14-18 重合但 2/5 行被 OCR 噪声打乱（→ weak），
    # 并填补 19-29 缺口（run 没有这些行）。
    orphan_lines = dict(_seg(14, 29))
    orphan_lines[15] = "noise token fifteen"
    orphan_lines[17] = "noise token seventeen"
    pcs.append(_pc("orphan", 0, "x/garbage.cc", orphan_lines))
    ledgers[("orphan", 0)] = LineLedger(
        page_stem="orphan", column_index=0, entries=_entries(orphan_lines),
    )
    sources = group_into_files(pcs, ledgers, GroupingConfig())
    assert not any(s.filename == "garbage.cc" for s in sources)  # 已救援
    real = next(s for s in sources if s.filename == "real.cc")
    assert "code.group.cross_bucket_rescued_weak" in real.flags


def test_weak_overlap_without_gap_not_rescued() -> None:
    """weak 重合但不填补任何缺口（孤页全在 run 行号范围内）→ 不救援。"""
    pcs: list[PageColumn] = []
    ledgers: dict[tuple[str, int], LineLedger] = {}
    _run_pages(pcs, ledgers, "a/b/real.cc")  # 连续覆盖 1-42，无缺口
    orphan_lines = dict(_seg(20, 24))  # 全部落在 run 已有行号内
    orphan_lines[21] = "noise alpha"
    orphan_lines[23] = "noise beta"
    pcs.append(_pc("orphan", 0, "x/garbage.cc", orphan_lines))
    ledgers[("orphan", 0)] = LineLedger(
        page_stem="orphan", column_index=0, entries=_entries(orphan_lines),
    )
    sources = group_into_files(pcs, ledgers, GroupingConfig())
    assert len(sources) == 2  # weak 但无桥接 → 不并
    orphan = next(s for s in sources if s.filename == "garbage.cc")
    assert "code.group.orphan_unrescued" in orphan.flags


def test_within_group_overlap_confirmed_flag() -> None:
    """多页同文件且重合内容一致 → 标 overlap_confirmed。"""
    pcs: list[PageColumn] = []
    ledgers: dict[tuple[str, int], LineLedger] = {}
    _page(pcs, ledgers, "p0", 0, "a/b/foo.cc", _seg(1, 10))
    _page(pcs, ledgers, "p1", 0, "a/b/foo.cc", _seg(8, 18))
    sources = group_into_files(pcs, ledgers, GroupingConfig())
    assert len(sources) == 1
    assert "code.group.overlap_confirmed" in sources[0].flags


def test_backward_compatible_without_ledgers() -> None:
    """不传 ledgers → 无救援、无 overlap flag，行为同纯文件名归类。"""
    pcs: list[PageColumn] = []
    ledgers: dict[tuple[str, int], LineLedger] = {}
    _run_pages(pcs, ledgers, "a/b/real.cc")
    _page(pcs, ledgers, "orphan", 0, "x/giesz.cc", _seg(20, 28))
    sources = group_into_files(pcs)  # 不传 ledgers
    assert len(sources) == 2  # 未救援
    assert all(
        not f.startswith("code.group.overlap")
        and "code.group.cross_bucket_rescued" not in f
        for s in sources for f in s.flags
    )
