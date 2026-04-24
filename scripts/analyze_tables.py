# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

# mypy: ignore-errors
# 一次性分析脚本：用 dict[str, object] 灵活存表格属性，不值得为 mypy
# 严格类型而做大改造；运行时正确性靠手测覆盖。

"""分析 markdown 中 HTML <table> 的结构问题。

侧重三类毛病（用户反馈：大型/长表格常见）：
1. **行列数失衡**：同一个表内不同 <tr> 的 <td> 数不一致，意味着写错列
2. **rowspan/colspan 误用**：rowspan="N" 紧跟单元格但实际后续行数对不上
3. **跨段重复表**：整篇内多个表的内容近乎一致（拍照重叠 + LLM 没去重）

用法：
    python scripts/analyze_tables.py output/verify-browser-1
    python scripts/analyze_tables.py output/verify-browser-1 output/verify-browser-2
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

_TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(
    r"<t[hd]\b([^>]*)>(.*?)</t[hd]>",
    re.DOTALL | re.IGNORECASE,
)
_ROWSPAN_RE = re.compile(r'rowspan\s*=\s*["\']?(\d+)', re.IGNORECASE)
_COLSPAN_RE = re.compile(r'colspan\s*=\s*["\']?(\d+)', re.IGNORECASE)


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def parse_table(html: str) -> dict[str, object]:
    """把一个 <table>...</table> 解析成结构化数据。

    返回:
        {
            "rows": int,                  # <tr> 计数
            "cells_per_row": list[int],   # 每行的"显式"单元格数（不算 rowspan 占位）
            "with_colspan": int,          # 含 colspan 的 cell 数
            "with_rowspan": int,
            "text_signature": str,        # 去 HTML 后的纯文本（用于跨表查重）
            "first_cell_preview": str,    # 第一个非空 cell 的前 30 字
        }
    """
    rows = []
    with_colspan = 0
    with_rowspan = 0
    text_parts = []
    first_preview = ""
    for tr_match in _TR_RE.finditer(html):
        tr_body = tr_match.group(1)
        cells = list(_CELL_RE.finditer(tr_body))
        rows.append(len(cells))
        for cm in cells:
            attrs, body = cm.group(1), cm.group(2)
            text = _strip_html(body)
            text_parts.append(text)
            if not first_preview and text:
                first_preview = text[:30]
            if _COLSPAN_RE.search(attrs):
                with_colspan += 1
            if _ROWSPAN_RE.search(attrs):
                with_rowspan += 1
    sig = " | ".join(text_parts)
    return {
        "rows": len(rows),
        "cells_per_row": rows,
        "with_colspan": with_colspan,
        "with_rowspan": with_rowspan,
        "text_signature": sig,
        "first_cell_preview": first_preview,
    }


def column_imbalance_score(cells_per_row: list[int]) -> tuple[int, int]:
    """返回 (出现频率最高的列数, 偏离主流列数 ≥ 2 的行数)。

    含 rowspan 的合理表：少数行列数会少 1（被上方 rowspan 占位）—— 这是
    HTML 表格的合法结构，不算"写错列"。所以阈值用 |dev| ≥ 2 而非 ≥ 1，
    把 rowspan 单格占位的合理少 1 排除，只抓真正的"行内列数差距 ≥ 2"。
    """
    if not cells_per_row:
        return (0, 0)
    counter = Counter(cells_per_row)
    most = counter.most_common(1)[0][0]
    deviated = sum(c for v, c in counter.items() if abs(v - most) >= 2)
    return (most, deviated)


def find_duplicate_tables(
    tables: list[dict[str, object]],
    sim_threshold: float = 0.85,
) -> list[tuple[int, int, float]]:
    """两两比较 text_signature；相似度 ≥ threshold 视为重复。"""
    dups: list[tuple[int, int, float]] = []
    for i in range(len(tables)):
        sig_i = str(tables[i]["text_signature"])
        for j in range(i + 1, len(tables)):
            sig_j = str(tables[j]["text_signature"])
            if not sig_i or not sig_j:
                continue
            ratio = SequenceMatcher(None, sig_i, sig_j).ratio()
            if ratio >= sim_threshold:
                dups.append((i, j, ratio))
    return dups


def analyze_doc(md_path: Path) -> dict[str, object]:
    text = md_path.read_text(encoding="utf-8")
    tables = [
        parse_table(m.group(1))
        for m in _TABLE_RE.finditer(text)
    ]

    imbalance_count = 0
    long_table_count = 0  # rows ≥ 10 视为大表
    for t in tables:
        cells: list[int] = t["cells_per_row"]  # type: ignore[assignment]
        most, dev = column_imbalance_score(cells)
        # 误报过滤：如果表用了 rowspan，且偏离的行只是"少数 1 单元格行"
        # （即被上方 rowspan 占位），不算失衡
        if dev > 0:
            has_rowspan = (t.get("with_rowspan") or 0) > 0
            min_cells = min(cells) if cells else 0
            if has_rowspan and min_cells == 1 and dev <= 2:
                pass
            else:
                imbalance_count += 1
        if t["rows"] >= 10:
            long_table_count += 1

    dups = find_duplicate_tables(tables)
    return {
        "doc": md_path.parent.name,
        "table_count": len(tables),
        "long_tables": long_table_count,
        "imbalanced": imbalance_count,
        "duplicate_pairs": len(dups),
        "duplicate_details": [
            {
                "i": i,
                "j": j,
                "ratio": round(r, 3),
                "rows_i": tables[i]["rows"],
                "rows_j": tables[j]["rows"],
                "preview": tables[i]["first_cell_preview"],
            }
            for i, j, r in dups
        ],
        "tables": tables,
    }


def report_one_root(root: Path, label: str = "") -> dict[str, object]:
    print(f"\n{'=' * 60}")
    print(f"  {label or root.name}")
    print("=" * 60)
    results = []
    for doc_dir in sorted(root.iterdir()):
        md = doc_dir / "document.md"
        if not md.exists():
            continue
        info = analyze_doc(md)
        results.append(info)
        print(
            f"  {info['doc']:30s}: tables={info['table_count']:3d}  "
            f"长表(≥10行)={info['long_tables']:2d}  "
            f"列失衡={info['imbalanced']:2d}  "
            f"重复对={info['duplicate_pairs']:2d}",
        )
        for d in info["duplicate_details"]:  # type: ignore[index]
            print(
                f"      重复表 #{d['i']}↔#{d['j']} sim={d['ratio']:.2f} "
                f"({d['rows_i']}行↔{d['rows_j']}行) {d['preview']!r}",
            )
    return {"label": label, "docs": results}


def diff_two_rounds(
    r1: dict[str, object], r2: dict[str, object],
) -> None:
    print(f"\n{'=' * 60}")
    print("  跨轮对比")
    print("=" * 60)
    by_doc1 = {d["doc"]: d for d in r1["docs"]}  # type: ignore[index]
    by_doc2 = {d["doc"]: d for d in r2["docs"]}  # type: ignore[index]
    common = sorted(set(by_doc1) & set(by_doc2))
    print(
        f"  {'doc':30s} {'tables(r1→r2)':18s} {'失衡(r1→r2)':14s} "
        f"{'重复对(r1→r2)':14s}",
    )
    for d in common:
        a, b = by_doc1[d], by_doc2[d]
        tc = f"{a['table_count']}→{b['table_count']}"
        im = f"{a['imbalanced']}→{b['imbalanced']}"
        dp = f"{a['duplicate_pairs']}→{b['duplicate_pairs']}"
        print(f"  {d:30s} {tc:18s} {im:14s} {dp:14s}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    roots = [Path(p) for p in sys.argv[1:]]
    rounds = []
    for i, r in enumerate(roots):
        rounds.append(report_one_root(r, label=f"round {i + 1}: {r.name}"))
    if len(rounds) >= 2:
        diff_two_rounds(rounds[0], rounds[1])


if __name__ == "__main__":
    main()
