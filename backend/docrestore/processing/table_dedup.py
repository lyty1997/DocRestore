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

"""HTML <table> 程序化去重（post-final_refine 兜底）。

场景：
- 拍照跨页重叠会让同一张表格在 OCR 后被识别两次（前页末尾 + 后页开头）
- 每张表的 HTML 字面很长（数百到数千字符），跨段被分到不同 LLM segment 时
  互相看不到，final_refine 看整篇时又被 30k+ token 压力下漏判重复
- LLM 去重不稳定（gpt-5.4-mini 两轮实测：U-Boot 稳定有 4-8 对重复表）

策略：
- 表格是结构化的，可以用 cell 纯文本两两 SequenceMatcher 精确比对
- sim ≥ threshold（默认 0.95）视为重复，保留更长（更完整）的那份
- 相邻 table 之间的正文保持原样不改
- 0 LLM 成本，100% 可重现

关键约束：
- 只用表格单元格的纯文本作为 signature，规避 LLM 改写 HTML 属性/空白
- 保留最长版本：拍照重叠场景下，后识别的版本往往更完整（前页末尾只拍到
  表头几行，后页才拍到完整表）—— 取长能修复这类"半截 + 完整并存"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


@dataclass
class _TableEntry:
    start: int
    end: int
    text: str
    sig: str
    cell_count: int


_TABLE_RE = re.compile(
    r"<table\b[^>]*>.*?</table>",
    re.DOTALL | re.IGNORECASE,
)
_TR_RE = re.compile(
    r"<tr\b[^>]*>(.*?)</tr>",
    re.DOTALL | re.IGNORECASE,
)
_CELL_RE = re.compile(
    r"<t[hd]\b[^>]*>(.*?)</t[hd]>",
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _table_signature(table_html: str) -> str:
    """把一个 <table>...</table> 压成单元格纯文本串，用于相似度比对。

    去所有 HTML 标签 + 压缩空白。两张表格如果单元格内容相同（哪怕属性不同、
    空白不同），signature 相同 → sim=1.0。
    """
    cells: list[str] = []
    for tr in _TR_RE.finditer(table_html):
        for cell in _CELL_RE.finditer(tr.group(1)):
            text = _TAG_RE.sub("", cell.group(1))
            text = " ".join(text.split())
            if text:
                cells.append(text)
    return " | ".join(cells)


def _build_entries(markdown: str) -> list[_TableEntry]:
    entries: list[_TableEntry] = []
    for m in _TABLE_RE.finditer(markdown):
        sig = _table_signature(m.group(0))
        cell_count = sig.count(" | ") + 1 if sig else 0
        entries.append(_TableEntry(
            start=m.start(),
            end=m.end(),
            text=m.group(0),
            sig=sig,
            cell_count=cell_count,
        ))
    return entries


def _find_similar_pairs(
    entries: list[_TableEntry],
    *,
    sim_threshold: float,
    min_cells: int,
) -> list[tuple[int, int, float]]:
    """两两比 entries，返回 sim ≥ threshold 的 (i, j, ratio)。"""
    pairs: list[tuple[int, int, float]] = []
    n = len(entries)
    for i in range(n):
        ent_i = entries[i]
        if not ent_i.sig or ent_i.cell_count < min_cells:
            continue
        for j in range(i + 1, n):
            ent_j = entries[j]
            if not ent_j.sig or ent_j.cell_count < min_cells:
                continue
            # 快路径：长度差超过阈值范围不可能 ≥ 阈值
            len_i, len_j = len(ent_i.sig), len(ent_j.sig)
            if min(len_i, len_j) / max(len_i, len_j) < sim_threshold - 0.05:
                continue
            ratio = SequenceMatcher(None, ent_i.sig, ent_j.sig).ratio()
            if ratio >= sim_threshold:
                pairs.append((i, j, ratio))
    return pairs


def _group_by_union_find(
    n: int, pairs: list[tuple[int, int, float]],
) -> dict[int, list[int]]:
    """并查集：把相似对连通成组。"""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    groups: dict[int, list[int]] = {}
    for x in range(n):
        groups.setdefault(find(x), []).append(x)
    return groups


def dedup_html_tables(
    markdown: str,
    *,
    sim_threshold: float = 0.95,
    min_cells: int = 3,
) -> tuple[str, list[dict[str, object]]]:
    """扫 markdown 中所有 <table>，删除重复副本（保留最长的）。

    Args:
        markdown: 含 <table> 的完整 markdown 文本
        sim_threshold: 两表 signature 相似度 ≥ 此值判为重复（默认 0.95）
        min_cells: 单元格数 < 此值的表跳过（短表 false positive 风险高）

    Returns:
        `(new_markdown, removed_records)`
        removed_records 每条形如
        `{"kept_index": int, "removed_index": int, "sim": float,
          "preview": str}`，供 quality report 记录。
    """
    entries = _build_entries(markdown)
    if len(entries) < 2:
        return markdown, []

    pairs_similar = _find_similar_pairs(
        entries, sim_threshold=sim_threshold, min_cells=min_cells,
    )
    if not pairs_similar:
        return markdown, []

    groups = _group_by_union_find(len(entries), pairs_similar)
    to_remove, removed_records = _select_dups_per_group(
        entries, groups, pairs_similar,
    )
    if not to_remove:
        return markdown, []

    new_md = _excise_tables(
        markdown,
        sorted(
            ((entries[idx].start, entries[idx].end) for idx in to_remove),
            reverse=True,
        ),
    )

    logger.info(
        "HTML 表格去重：从 %d 张表中移除了 %d 张重复表",
        len(entries), len(to_remove),
    )
    return new_md, removed_records


def _select_dups_per_group(
    entries: list[_TableEntry],
    groups: dict[int, list[int]],
    pairs_similar: list[tuple[int, int, float]],
) -> tuple[set[int], list[dict[str, object]]]:
    """各组内排序保留最长，其余记入 to_remove + records。"""
    to_remove: set[int] = set()
    records: list[dict[str, object]] = []
    for member_list in groups.values():
        if len(member_list) < 2:
            continue
        # 排序：cell_count 大优先；tie 时 index 小（先出现）优先
        member_list.sort(
            key=lambda idx: (-entries[idx].cell_count, idx),
        )
        keeper = member_list[0]
        for dup in member_list[1:]:
            to_remove.add(dup)
            sim = next(
                (r for a, b, r in pairs_similar
                 if {a, b} == {keeper, dup}),
                0.0,
            )
            records.append({
                "kept_index": keeper,
                "removed_index": dup,
                "sim": round(sim, 3),
                "kept_cells": entries[keeper].cell_count,
                "removed_cells": entries[dup].cell_count,
                "preview": entries[dup].sig[:60],
            })
    return to_remove, records


def _excise_tables(
    markdown: str, removal_spans: list[tuple[int, int]],
) -> str:
    """从 markdown 中按 (start, end) 范围抠掉，清理多余空白行。"""
    new_md = markdown
    for start, end in removal_spans:
        left = start
        while left > 0 and new_md[left - 1] in (" ", "\t"):
            left -= 1
        while left > 0 and new_md[left - 1] == "\n":
            left -= 1
        right = end
        while right < len(new_md) and new_md[right] in (" ", "\t"):
            right += 1
        while right < len(new_md) and new_md[right] == "\n":
            right += 1
        replacement = "\n\n" if (left > 0 and right < len(new_md)) else ""
        new_md = new_md[:left] + replacement + new_md[right:]
    return new_md
