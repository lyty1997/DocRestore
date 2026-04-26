# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""跨张文件归类（AGE-8 Phase 2.3）

把"N 张图 × M 栏 = N×M 个 PageColumn"按文件路径聚到同一 source file，
同源文件内按行号排序拼接（重叠去重）。

**核心算法**：
  1. 跨张 path/filename canonical 标准化：
     - fuzzy filename 用 ``lower()`` 容忍 OCR 大小写错识（BUILD/BUiLD/BUlLD）
     - dir 用 "去 / 后" 后缀兼容判定，把 ``gpu/openmax`` 与 ``media/gpu/openmax``
       识别为同 dir（短的是长的后缀，单图无 peer 时缺前缀场景由此兜底）
     - 同组内 canonical filename = 字符长度+频次最大；canonical dir = 段数最多
  2. 按 (canonical_dir, canonical_filename) 二级分组
  3. 同文件内按行号排序，line_no 重复取首次（多张图重叠区域去重）
  4. 行号 gap（OCR 漏识 / 拍照漏页）→ flag ``code.line_gap`` + 占位

**约束**（用户决策 #3）：
  同图不同栏 ≠ 同文件 → 跨栏只通过 (path, filename) 配对，不靠"内容相邻"

**输入约定**：caller 把每张图每栏组装成 PageColumn 传入。
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docrestore.processing.code_assembly import CodeColumn
    from docrestore.processing.ide_meta_extract import IDEMeta

logger = logging.getLogger(__name__)


@dataclass
class PageColumn:
    """跨张归类的输入：一张图的一栏"""

    page_stem: str
    column_index: int
    meta: IDEMeta
    column: CodeColumn


@dataclass
class SourceFile:
    """跨张聚合后的源文件"""

    path: str                 # canonical path（dir/filename 或仅 filename）
    filename: str
    language: str | None
    pages: list[PageColumn]   # 来源（按行号顺序）
    merged_text: str          # 拼接后代码
    line_count: int
    line_no_range: tuple[int, int]
    flags: list[str] = field(default_factory=list)


def group_into_files(page_columns: list[PageColumn]) -> list[SourceFile]:
    """跨张归类入口

    返回的 SourceFile 列表按 path 字典序排序，便于稳定输出。
    """
    if not page_columns:
        return []

    # 1. fuzzy filename 一级聚类
    by_filename: dict[str, list[PageColumn]] = {}
    no_filename: list[PageColumn] = []
    for pc in page_columns:
        if not pc.meta.filename:
            no_filename.append(pc)
            continue
        key = _fuzzy_filename_key(pc.meta.filename)
        by_filename.setdefault(key, []).append(pc)

    # 2. 每个 filename 组内按 dir 兼容性二级聚类
    files: list[SourceFile] = []
    for group in by_filename.values():
        for sub_group in _split_by_compatible_dir(group):
            # 决策 #3 硬约束：同 page_stem 不同 column_index 必须拆开
            # （AGE-45 偶发把同图两栏识别为同 file，这里兜底拒绝合并）
            for one_page_group in _enforce_one_page_one_file(sub_group):
                files.append(_build_source_file(one_page_group))

    # 3. 处理 filename 缺失的（quality 信号 + 单独成组）
    for pc in no_filename:
        files.append(_build_source_file(
            [pc], extra_flags=["code.grouping.no_filename"],
        ))

    # 4. path 去重：决策 #3 拆出来的多个 sub_group 可能 canonical_path 相同
    # （都是 status.h），加 :col<i> 后缀避免 AGE-47 写文件时覆盖
    _disambiguate_duplicate_paths(files)

    files.sort(key=lambda f: f.path)
    return files


def _disambiguate_duplicate_paths(files: list[SourceFile]) -> None:
    """同 path 多 SourceFile 时给后续的加 ``:col<idx>`` 后缀（in-place）"""
    seen: dict[str, int] = {}
    for src in files:
        if src.path not in seen:
            seen[src.path] = 1
            continue
        seen[src.path] += 1
        col_indices = sorted({pc.column_index for pc in src.pages})
        suffix = f"__col{col_indices[0]}"
        # 把后缀插到扩展名前：foo.cc → foo__col1.cc
        if "." in src.filename:
            base, ext = src.filename.rsplit(".", 1)
            new_filename = f"{base}{suffix}.{ext}"
        else:
            new_filename = f"{src.filename}{suffix}"
        # 同步改 path
        if "/" in src.path:
            head = src.path.rsplit("/", 1)[0]
            src.path = f"{head}/{new_filename}"
        else:
            src.path = new_filename
        src.filename = new_filename
        src.flags.append("code.grouping.disambiguated_by_column")


def _fuzzy_filename_key(name: str) -> str:
    """OCR 视觉混淆容错的 filename 归一 key

    把 ``I/l/1/|`` 都映射到同字符、``O/0`` 映射到同字符，让
    ``BUILD.gn`` / ``BUiLD.gn`` / ``BUlLD.gn`` / ``BUlLD.gn`` 等 OCR 字符
    级噪声变体落在同一桶里。
    """
    s = name.lower()
    for ch in ("i", "l", "1", "|"):
        s = s.replace(ch, "*")
    for ch in ("o", "0"):
        s = s.replace(ch, "@")
    return s


def _enforce_one_page_one_file(
    group: list[PageColumn],
) -> list[list[PageColumn]]:
    """决策 #3 硬约束：同 page_stem 多个 column_index 必须分组

    一张图同 file 不可能出现两次（IDE 不允许同 file 在两个 split editor 栏），
    若 AGE-45 错识致同 page 多 column 进同组 → 这里强制按 column_index 拆。

    返回 ``max(per_page_count)`` 个子组：
      - sub_group[0]：每张图的 column_index 最小那个
      - sub_group[1]：每张图的 column_index 第二小（如有）
      - ...
    """
    by_page: dict[str, list[PageColumn]] = {}
    for pc in group:
        by_page.setdefault(pc.page_stem, []).append(pc)

    if all(len(cols) == 1 for cols in by_page.values()):
        return [group]

    max_cols = max(len(cols) for cols in by_page.values())
    sub_groups: list[list[PageColumn]] = [[] for _ in range(max_cols)]
    for cols in by_page.values():
        for slot, pc in enumerate(sorted(cols, key=lambda c: c.column_index)):
            sub_groups[slot].append(pc)
    return [g for g in sub_groups if g]


def _split_by_compatible_dir(
    group: list[PageColumn],
) -> list[list[PageColumn]]:
    """同 filename 内，按 dir 兼容性细分子组

    兼容性：``dir1.replace('/', '') == dir2.replace('/', '')``（OCR 漏分隔
    符）或 一方是另一方的后缀（如 ``gpu/openmax`` ⊆ ``media/gpu/openmax``）。
    """
    if len(group) <= 1:
        return [group]

    compacts = [_compact_dir(pc.meta.path) for pc in group]
    sub_groups: list[set[int]] = []
    for i, c1 in enumerate(compacts):
        placed = False
        for sg in sub_groups:
            if any(_dirs_compatible(c1, compacts[j]) for j in sg):
                sg.add(i)
                placed = True
                break
        if not placed:
            sub_groups.append({i})
    return [[group[i] for i in sg] for sg in sub_groups]


def _compact_dir(path: str | None) -> str:
    """从 ``media/gpu/openmax/foo.cc`` 提 ``mediagpuopenmax``（去 / 大小写不变）"""
    if not path or "/" not in path:
        return ""
    return path.rsplit("/", 1)[0].replace("/", "")


def _dirs_compatible(c1: str, c2: str) -> bool:
    """两个 compact dir 是否兼容：相等 / 一方是另一方后缀 / 任一方为空"""
    if c1 == c2:
        return True
    if not c1 or not c2:
        return True
    return c1.endswith(c2) or c2.endswith(c1)


def _build_source_file(
    group: list[PageColumn],
    *,
    extra_flags: list[str] | None = None,
) -> SourceFile:
    """从同文件多 PageColumn 构造 SourceFile"""
    # canonical filename：长度 + 频次最大
    filenames = [pc.meta.filename for pc in group if pc.meta.filename]
    canonical_filename = (
        max(filenames, key=lambda f: (len(f), filenames.count(f)))
        if filenames
        else "_unknown"
    )

    # canonical dir：段数最多 + 出现频次最大
    dirs = [
        pc.meta.path.rsplit("/", 1)[0]
        for pc in group
        if pc.meta.path and "/" in pc.meta.path
    ]
    canonical_dir: str | None = None
    if dirs:
        dir_counter = Counter(dirs)
        canonical_dir = max(
            dirs, key=lambda d: (d.count("/"), dir_counter[d]),
        )
    canonical_path = (
        f"{canonical_dir}/{canonical_filename}"
        if canonical_dir else canonical_filename
    )

    # canonical language：第一个非空
    language: str | None = None
    for pc in group:
        if pc.meta.language:
            language = pc.meta.language
            break

    # 按行号合并代码
    merged_text, line_no_range, gap_flags = _merge_columns_by_line_no(group)

    flags: list[str] = list(extra_flags or [])
    flags.extend(gap_flags)
    if len(group) > 1:
        flags.append(f"code.grouping.merged_pages={len(group)}")

    # pages 按 line_no_range 起点排序
    sorted_pages = sorted(
        group,
        key=lambda pc: _column_line_no_start(pc.column),
    )

    line_count = (
        merged_text.count("\n") + 1 if merged_text else 0
    )

    return SourceFile(
        path=canonical_path,
        filename=canonical_filename,
        language=language,
        pages=sorted_pages,
        merged_text=merged_text,
        line_count=line_count,
        line_no_range=line_no_range,
        flags=flags,
    )


def _column_line_no_start(column: CodeColumn) -> int:
    """CodeColumn 的起始行号（用于多张图排序）"""
    if not column.lines:
        return 0
    return min(line.line_no for line in column.lines)


def _merge_columns_by_line_no(
    group: list[PageColumn],
) -> tuple[str, tuple[int, int], list[str]]:
    """按 line_no 合并多个 column，重复 line 取首次出现"""
    by_line_no: dict[int, str] = {}  # line_no -> 渲染后行（含缩进）
    for pc in group:
        for line in pc.column.lines:
            if line.line_no in by_line_no:
                continue  # 重叠：保留首次（先到的图）
            rendered = " " * line.indent + line.text
            by_line_no[line.line_no] = rendered
    if not by_line_no:
        return "", (0, 0), []

    sorted_nos = sorted(by_line_no)
    lo, hi = sorted_nos[0], sorted_nos[-1]

    # 检测行号 gap（OCR 漏识或拍照漏页）
    flags: list[str] = []
    expected = set(range(lo, hi + 1))
    actual = set(sorted_nos)
    missing = sorted(expected - actual)
    if missing:
        flags.append(f"code.grouping.missing_line_nos={len(missing)}")

    parts: list[str] = []
    prev_no = lo - 1
    for no in sorted_nos:
        gap = no - prev_no - 1
        if gap > 0:
            # 占位空行（不强插内容，让 LLM 阶段查原图补）
            parts.extend([""] * gap)
        parts.append(by_line_no[no])
        prev_no = no
    return "\n".join(parts), (lo, hi), flags
