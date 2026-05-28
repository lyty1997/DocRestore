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

"""H2 章节程序化去重（post-final_refine 兜底，配合 table_dedup）。

场景：
- 跨页拍照重叠：page A 末尾出现「## 编译方式 + 半截被截断的正文」+ page B
  开头出现「## 编译方式 + 完整的正文」 → final_refine 偶尔识别不出
- LLM 重做 (signal 4) 也只能改善到 1-2 个剩余（实测 4/6 doc 留有重复 H2）

合并判定（双轨）：
- 路径 A — **近乎相同**：`SequenceMatcher.ratio() ≥ 0.95`
  覆盖完全相同的拍照重复
- 路径 B — **截断 + 完整**：dup 显著更短（≤ keeper 的 70%）且 dup 的字符
  ≥ 90% 都包含在 keeper 中（asymmetric overlap）
  覆盖"半截 OCR 内容 + 后页完整版"的常见场景

策略（满足任一路径）：
- 保留 body 最长的那份（更完整）
- 删除其他副本及其 body 到下一个 H1/H2 之前的所有内容

不动：
- 单独出现的 H2
- H1 / H3+
- table（已在 table_dedup 处理）
- 同名 H2 但内容差异大（真"同名不同章节"，罕见但合法）

为什么不用单一 sim 阈值：
- 0.6-0.8 阈值会误删「内容相同但结尾几行不同」的章节
  （body 拼到下个 H2 之前，可能尾部含本来不属于本节的"邻居正文"）
- 0.95 阈值漏掉「截断半截 + 完整」（前缀同但后半完全缺失，ratio≈0.67）
- 双轨能同时覆盖两类，且各自风险都低
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


@dataclass
class _H2Section:
    h2_start: int
    section_end: int
    title: str
    body: str


_H2_LINE_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
#: 章节边界：H1 / H2（同级或更高级）。H3+ 视为节内子分块，不切。
_SECTION_BOUNDARY_RE = re.compile(r"^(#|##)\s+", re.MULTILINE)
#: HTML 注释（page marker / GAP 等）—— 比对正文相似度时剥掉，仅是元数据
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _normalize_body_for_compare(body: str) -> str:
    """剥 HTML 注释（page marker、GAP 注释等）+ 压空白，用于相似度比对。

    比 body 原文短：避免章节体在 strip 后还混入"<!-- page: xxx -->"等
    metadata，干扰 SequenceMatcher 的 ratio 计算。
    """
    cleaned = _HTML_COMMENT_RE.sub("", body)
    return " ".join(cleaned.split())


def _normalize_title(s: str) -> str:
    """归一化标题：压空白 + 去尾标点。"""
    return " ".join(s.split()).rstrip("：:。.")


def _section_body(markdown: str, h2_end: int) -> tuple[int, str]:
    """从 h2_end（标题行尾）开始，找到下个 H1/H2 之前的内容。

    返回 (body_end_pos, body_text)。body_end_pos 是下个章节边界的起始位置
    （或文末），body_text 已 strip。
    """
    next_match = _SECTION_BOUNDARY_RE.search(markdown, pos=h2_end)
    body_end = next_match.start() if next_match else len(markdown)
    return body_end, markdown[h2_end:body_end].strip()


def _should_merge(
    body_a: str,
    body_b: str,
    *,
    near_identical_threshold: float = 0.98,
    asymmetric_overlap_threshold: float = 0.9,
    truncation_length_ratio: float = 0.7,
    ending_check_chars: int = 12,
) -> tuple[bool, float, str]:
    """判定两节 body 是否应合并。

    双轨：
    - **near_identical**：`SequenceMatcher.ratio() ≥ 0.98` AND 末尾几字符
      相同。两节几乎完全一样的拍照重复才合并；中间区间（0.95-0.98）一
      些"长度相同但局部内容不同"的 false positive 被保留。
    - **truncated_prefix**：dup 显著短（≤ keeper 70%）+ dup 90% 字符
      包含在 keeper 里。覆盖"半截 OCR + 后页完整版"。

    返回 `(should_merge, score, reason)`。
    reason ∈ {"identical", "near_identical", "truncated_prefix", "no_match"}。
    """
    if not body_a and not body_b:
        return True, 1.0, "identical"
    if not body_a or not body_b:
        return True, 0.5, "identical"

    matcher = SequenceMatcher(None, body_a, body_b)
    ratio = matcher.ratio()

    # 路径 A：近乎相同 + 末尾匹配（防"前段同后段不同"误删）
    if ratio >= near_identical_threshold:
        tail_a = body_a[-ending_check_chars:]
        tail_b = body_b[-ending_check_chars:]
        if tail_a == tail_b:
            return True, ratio, "near_identical"

    # 路径 B：dup（短的）的内容大部分包含在 keeper（长的）里
    short, long_ = (body_a, body_b) if len(body_a) <= len(body_b) else (body_b, body_a)
    if not short:
        return True, 1.0, "identical"
    is_significantly_shorter = len(short) <= len(long_) * truncation_length_ratio
    if is_significantly_shorter:
        m2 = SequenceMatcher(None, short, long_)
        match_size = sum(b.size for b in m2.get_matching_blocks())
        asymm = match_size / len(short)
        if asymm >= asymmetric_overlap_threshold:
            return True, asymm, "truncated_prefix"

    return False, ratio, "no_match"


def _build_sections(markdown: str) -> list[_H2Section]:
    sections: list[_H2Section] = []
    for m in _H2_LINE_RE.finditer(markdown):
        body_end, body = _section_body(markdown, m.end())
        sections.append(_H2Section(
            h2_start=m.start(),
            section_end=body_end,
            title=_normalize_title(m.group(1)),
            body=body,
        ))
    return sections


def _union_find_groups(
    n: int, edges: list[tuple[int, int]],
) -> dict[int, list[int]]:
    parent = list(range(n))

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    groups: dict[int, list[int]] = {}
    for x in range(n):
        groups.setdefault(find(x), []).append(x)
    return groups


def _process_title_group(
    sections: list[_H2Section],
    idxs: list[int],
    title: str,
) -> tuple[set[int], list[dict[str, object]]]:
    """对同 title 的一组 H2 索引做合并判定，返回要删的全局索引 + 记录。"""
    n_local = len(idxs)
    edges: list[tuple[int, int]] = []
    decision: dict[tuple[int, int], tuple[float, str]] = {}
    for a in range(n_local):
        body_a = _normalize_body_for_compare(sections[idxs[a]].body)
        for b in range(a + 1, n_local):
            body_b = _normalize_body_for_compare(sections[idxs[b]].body)
            ok, score, reason = _should_merge(body_a, body_b)
            decision[(a, b)] = (score, reason)
            if ok:
                edges.append((a, b))
    if not edges:
        return set(), []

    groups_local = _union_find_groups(n_local, edges)
    to_remove: set[int] = set()
    records: list[dict[str, object]] = []
    for members in groups_local.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda li: (-len(sections[idxs[li]].body), idxs[li]),
        )
        keeper_local = members[0]
        keeper_global = idxs[keeper_local]
        for dup_local in members[1:]:
            dup_global = idxs[dup_local]
            a, b = sorted([keeper_local, dup_local])
            score, reason = decision.get((a, b), (0.0, "?"))
            to_remove.add(dup_global)
            records.append({
                "title": title,
                "kept_index": keeper_global,
                "removed_index": dup_global,
                "score": round(score, 3),
                "reason": reason,
                "kept_body_chars": len(sections[keeper_global].body),
                "removed_body_chars": len(sections[dup_global].body),
            })
    return to_remove, records


def dedup_h2_sections(
    markdown: str,
) -> tuple[str, list[dict[str, object]]]:
    """扫 markdown 中所有 H2 章节，删除重复副本（保留章节体最长的）。

    判定见 `_should_merge` 的双轨规则。

    Returns:
        `(new_markdown, removed_records)`
        removed_records 每条形如
        `{"title": str, "kept_index": int, "removed_index": int,
          "score": float, "reason": str,
          "kept_body_chars": int, "removed_body_chars": int}`
    """
    sections = _build_sections(markdown)
    if len(sections) < 2:
        return markdown, []

    by_title: dict[str, list[int]] = {}
    for i, s in enumerate(sections):
        by_title.setdefault(s.title, []).append(i)

    to_remove: set[int] = set()
    removed_records: list[dict[str, object]] = []
    for title, idxs in by_title.items():
        if len(idxs) < 2:
            continue
        sub_remove, sub_records = _process_title_group(
            sections, idxs, title,
        )
        to_remove.update(sub_remove)
        removed_records.extend(sub_records)

    if not to_remove:
        return markdown, []

    removal_spans = sorted(
        (
            (
                sections[i].h2_start,
                sections[i].section_end,
            )
            for i in to_remove
        ),
        reverse=True,
    )
    new_md = markdown
    for start, end in removal_spans:
        # 向前吃多余空行
        left = start
        while left > 0 and new_md[left - 1] == "\n":
            left -= 1
        # 向后吃多余空行
        right = end
        while right < len(new_md) and new_md[right] == "\n":
            right += 1
        if left > 0 and right < len(new_md):
            replacement = "\n\n"
        else:
            replacement = ""
        new_md = new_md[:left] + replacement + new_md[right:]

    logger.info(
        "H2 章节去重：从 %d 个 H2 中移除了 %d 个重复",
        len(sections), len(to_remove),
    )
    return new_md, removed_records
