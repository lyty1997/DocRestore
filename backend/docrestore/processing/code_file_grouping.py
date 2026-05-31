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
     - dir 用 "去 / 后" 后缀兼容判定，把 ``core/widget`` 与 ``app/core/widget``
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
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docrestore.processing.code_assembly import CodeColumn
    from docrestore.processing.code_line_ledger import LineEntry, LineLedger
    from docrestore.processing.ide_meta_extract import IDEMeta, PathCandidate

logger = logging.getLogger(__name__)


@dataclass
class PageColumn:
    """跨张归类的输入：一张图的一栏"""

    page_stem: str
    column_index: int
    meta: IDEMeta
    column: CodeColumn


@dataclass
class CodeSegment:
    """分组前的最小可审计代码来源单元。"""

    page_stem: str
    column_index: int
    bbox: tuple[int, int, int, int]
    line_no_range: tuple[int, int]
    path_candidates: list[PathCandidate]
    selected_path: str | None
    selected_path_confidence: float
    language: str | None
    flags: list[str] = field(default_factory=list)


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
    #: 行号 -> 贡献该行最终文本的来源页 stem（S3 可溯源；多页分歧时记胜出页）。
    line_provenance: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupingConfig:
    """S2 行号锚定归类阈值（pipeline 从 ``CodeRestoreConfig`` 映射传入）。"""

    overlap_min_lines: int = 3
    overlap_confirm_ratio: float = 0.90
    overlap_conflict_ratio: float = 0.50
    rescue_max_orphan_pages: int = 3
    line_match_ratio: float = 0.80  # 单行视相等的相似度下限


def segment_from_page_column(page: PageColumn) -> CodeSegment:
    """把现有 PageColumn 转成 AGE-62 CodeSegment 审计模型。"""
    return CodeSegment(
        page_stem=page.page_stem,
        column_index=page.column_index,
        bbox=page.column.bbox,
        line_no_range=(
            _column_line_no_start(page.column),
            _column_line_no_end(page.column),
        ),
        path_candidates=list(page.meta.path_candidates),
        selected_path=page.meta.path,
        selected_path_confidence=page.meta.path_confidence,
        language=page.meta.language,
        flags=[
            *page.meta.flags,
            *page.column.flags,
        ],
    )


def group_into_files(
    page_columns: list[PageColumn],
    ledgers: dict[tuple[str, int], LineLedger] | None = None,
    config: GroupingConfig | None = None,
) -> list[SourceFile]:
    """跨张归类入口（S2：行号锚定 + 跨桶救援）

    ``ledgers`` 为 S0 产出的 ``{(page_stem, column_index): LineLedger}``，供
    行号重合区内容一致性裁决；缺省（空）时退回纯文件名/路径归类（向后兼容）。
    返回的 SourceFile 列表按 path 字典序排序，便于稳定输出。
    """
    if not page_columns:
        return []
    cfg = config or GroupingConfig()
    ledger_map = ledgers or {}

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

    # 4. 同 dir 下 filename 极相似（OCR 字符噪声 / 截断）→ 小组并入大组
    files = _merge_near_duplicate_filenames(files)

    # 4.1 组内行号重合状态标注（仅打 flag，不移动页 → 零功能回归）
    _annotate_overlap_status(files, ledger_map, cfg)

    # 4.2 跨桶救援：文件名 garbage 的小碎片，靠行号重合区内容一致性归位
    files = _cross_bucket_rescue(files, ledger_map, cfg)

    # 5. path 去重：决策 #3 拆出来的多个 sub_group 可能 canonical_path 相同
    # （都是 status.h），加 :col<i> 后缀避免 AGE-47 写文件时覆盖
    _disambiguate_duplicate_paths(files)

    files.sort(key=lambda f: f.path)
    return files


def _add_flag(src: SourceFile, flag: str) -> None:
    if flag not in src.flags:
        src.flags.append(flag)


def _entries_of(
    pc: PageColumn, ledger_map: dict[tuple[str, int], LineLedger],
) -> dict[int, LineEntry]:
    """取某页栏的行账本 entries；缺失返回空 dict。"""
    ledger = ledger_map.get((pc.page_stem, pc.column_index))
    return ledger.entries if ledger is not None else {}


def _line_matches(text_a: str, text_b: str, cfg: GroupingConfig) -> bool:
    """两行归一空白后是否视为相同（相等或相似度 ≥ 阈值）。"""
    norm_a = " ".join(text_a.split())
    norm_b = " ".join(text_b.split())
    if norm_a == norm_b:
        return True
    if not norm_a or not norm_b:
        return False
    return SequenceMatcher(None, norm_a, norm_b).ratio() >= cfg.line_match_ratio


def _overlap_verdict(
    entries_a: dict[int, LineEntry],
    entries_b: dict[int, LineEntry],
    cfg: GroupingConfig,
) -> tuple[str, float, int]:
    """两页在共享行号上的内容一致裁决。

    仅取双方都 ``anchor_trustable`` 的共享行。返回 ``(verdict, ratio, n_shared)``，
    verdict ∈ {``confirm``, ``conflict``, ``weak``, ``insufficient``}。
    """
    shared: list[int] = []
    for line_no, entry_a in entries_a.items():
        if not entry_a.anchor_trustable:
            continue
        entry_b = entries_b.get(line_no)
        if entry_b is None or not entry_b.anchor_trustable:
            continue
        shared.append(line_no)
    if len(shared) < cfg.overlap_min_lines:
        return "insufficient", 0.0, len(shared)
    matches = sum(
        1 for line_no in shared
        if _line_matches(
            entries_a[line_no].text, entries_b[line_no].text, cfg,
        )
    )
    ratio = matches / len(shared)
    if ratio >= cfg.overlap_confirm_ratio:
        return "confirm", ratio, len(shared)
    if ratio <= cfg.overlap_conflict_ratio:
        return "conflict", ratio, len(shared)
    return "weak", ratio, len(shared)


def _annotate_overlap_status(
    files: list[SourceFile],
    ledger_map: dict[tuple[str, int], LineLedger],
    cfg: GroupingConfig,
) -> None:
    """标注多页文件组内相邻页的行号重合状态（仅打 flag，不移动页）。"""
    if not ledger_map:
        return
    for src in files:
        if len(src.pages) < 2:
            continue
        ordered = sorted(
            src.pages, key=lambda pc: _column_line_no_start(pc.column),
        )
        statuses: set[str] = set()
        for prev, curr in zip(ordered, ordered[1:], strict=False):
            verdict = _overlap_verdict(
                _entries_of(prev, ledger_map),
                _entries_of(curr, ledger_map),
                cfg,
            )[0]
            statuses.add(verdict)
        if "conflict" in statuses:
            _add_flag(src, "code.group.overlap_conflict")
        elif "confirm" in statuses:
            _add_flag(src, "code.group.overlap_confirmed")
        elif "weak" in statuses:
            _add_flag(src, "code.group.overlap_weak")
        else:
            _add_flag(src, "code.group.gap_no_overlap")


def _trustable_line_nos(
    src: SourceFile, ledger_map: dict[tuple[str, int], LineLedger],
) -> set[int]:
    """SourceFile 所有页中 anchor_trustable 的行号并集。"""
    out: set[int] = set()
    for pc in src.pages:
        for line_no, entry in _entries_of(pc, ledger_map).items():
            if entry.anchor_trustable:
                out.add(line_no)
    return out


def _orphan_matches_run(
    orphan: SourceFile,
    run: SourceFile,
    ledger_map: dict[tuple[str, int], LineLedger],
    cfg: GroupingConfig,
) -> tuple[int, int, float]:
    """orphan 与 run 的最佳重合匹配，返回 ``(tier, n_shared, ratio)``。

    tier 2 = 任一对页 ``confirm``（一致率 ≥ θ_high）；
    tier 1 = ``weak``（θ_low < 一致率 < θ_high，即多数行一致非冲突）**且**
    orphan 填补了 run 缺失的行号（结构桥接，决策 2026-05-31）；
    tier 0 = 不匹配。weak 必须叠加行号桥接才救援——两个不同文件极难同时满足
    「同行号多数内容一致」与「连续填补行号缺口」，比单纯降阈值安全。
    """
    fills_gap = bool(
        _trustable_line_nos(orphan, ledger_map)
        - _trustable_line_nos(run, ledger_map)
    )
    best: tuple[int, int, float] = (0, 0, 0.0)
    for orphan_page in orphan.pages:
        orphan_entries = _entries_of(orphan_page, ledger_map)
        if not orphan_entries:
            continue
        for run_page in run.pages:
            if (
                orphan_page.page_stem == run_page.page_stem
                and orphan_page.column_index == run_page.column_index
            ):
                continue  # 同一页栏不跨桶（防御）
            verdict, ratio, n_shared = _overlap_verdict(
                orphan_entries, _entries_of(run_page, ledger_map), cfg,
            )
            if verdict == "confirm":
                tier = 2
            elif verdict == "weak" and fills_gap:
                tier = 1
            else:
                continue
            best = max(best, (tier, n_shared, ratio))
    return best


def _cross_bucket_rescue(
    files: list[SourceFile],
    ledger_map: dict[tuple[str, int], LineLedger],
    cfg: GroupingConfig,
) -> list[SourceFile]:
    """文件名 garbage 的小碎片，靠行号重合区内容一致性归并进对应 run。

    confirm 或 weak+行号桥接 时救援；无命中标 ``code.group.orphan_unrescued``。
    无 ledger 时跳过（无内容证据，向后兼容）。
    """
    if not ledger_map or len(files) < 2:
        return files
    runs = [f for f in files if len(f.pages) > cfg.rescue_max_orphan_pages]
    orphans = [f for f in files if len(f.pages) <= cfg.rescue_max_orphan_pages]
    if not runs or not orphans:
        return files
    assignment = _assign_orphans_to_runs(orphans, runs, ledger_map, cfg)
    if not assignment:
        return files
    return _apply_rescue(files, orphans, assignment)


def _assign_orphans_to_runs(
    orphans: list[SourceFile],
    runs: list[SourceFile],
    ledger_map: dict[tuple[str, int], LineLedger],
    cfg: GroupingConfig,
) -> dict[int, tuple[SourceFile, int]]:
    """每个 orphan 选匹配最强的 run；无命中标 orphan_unrescued。

    返回 ``{id(orphan): (目标 run, tier)}``，tier 2=confirm / 1=weak+桥接。
    """
    assignment: dict[int, tuple[SourceFile, int]] = {}
    for orphan in orphans:
        best_run: SourceFile | None = None
        best_score: tuple[int, int, float] = (0, 0, 0.0)
        for run in runs:
            score = _orphan_matches_run(orphan, run, ledger_map, cfg)
            if score[0] > 0 and score > best_score:
                best_score = score
                best_run = run
        if best_run is not None:
            assignment[id(orphan)] = (best_run, best_score[0])
        else:
            _add_flag(orphan, "code.group.orphan_unrescued")
    return assignment


def _apply_rescue(
    files: list[SourceFile],
    orphans: list[SourceFile],
    assignment: dict[int, tuple[SourceFile, int]],
) -> list[SourceFile]:
    """把已分配的 orphan 页并入目标 run，重建受影响 run，丢弃被并 orphan。"""
    extra_pages: dict[int, list[PageColumn]] = {}
    weak_targets: set[int] = set()
    for orphan in orphans:
        entry = assignment.get(id(orphan))
        if entry is None:
            continue
        target, tier = entry
        extra_pages.setdefault(id(target), []).extend(orphan.pages)
        if tier == 1:
            weak_targets.add(id(target))
    result: list[SourceFile] = []
    for src in files:
        if id(src) in assignment:
            continue  # 该 orphan 已并入 run
        gained = extra_pages.get(id(src))
        if gained:
            extra_flags = ["code.group.cross_bucket_rescued"]
            if id(src) in weak_targets:
                extra_flags.append("code.group.cross_bucket_rescued_weak")
            result.append(_build_source_file(
                [*src.pages, *gained], extra_flags=extra_flags,
            ))
        else:
            result.append(src)
    return result


#: 小组并入大组时，小组 page 数占两组之和的最大比例。超过此比例视为
#: "两份真实不同的文件"，不合并。0.1 = 10% —— 经验值，page06873 typo
#: 案例里 typo 组占 1/(1+255)=0.39%，远低于阈值。
_NEAR_DUP_MAX_RATIO = 0.10

#: filename 编辑距离阈值（≤ 视为同一份文件）。OCR 单字符多/少识/
#: 误识在阈值内，全新文件名一般差距 > 2。
_NEAR_DUP_MAX_EDIT_DISTANCE = 2


def _merge_near_duplicate_filenames(  # noqa: C901 — 同步保护多条件 + 合并分支
    files: list[SourceFile],
) -> list[SourceFile]:
    """同 dir 下 filename 仅差 1-2 字符（OCR 噪声）或一方是另一方后缀
    （前缀截断）→ 小组并入大组（保留大组的 canonical 名称）。

    保护：
      - 必须同扩展名（``.cc`` 与 ``.h`` 永不合）
      - 必须 dir 兼容（``_compact_dir`` 等价或互为后缀）
      - 小组占比 ≤ ``_NEAR_DUP_MAX_RATIO``，否则视为真实独立文件
    """
    if len(files) < 2:
        return files

    # 按 (canonical_dir_compact, ext) 分桶：同桶内才尝试合并
    buckets: dict[tuple[str, str], list[int]] = {}
    for i, src in enumerate(files):
        ext = src.filename.rsplit(".", 1)[-1].lower() if "." in src.filename else ""
        dir_compact = (
            src.path.rsplit("/", 1)[0].replace("/", "")
            if "/" in src.path else ""
        )
        buckets.setdefault((dir_compact, ext), []).append(i)

    merged_into: dict[int, int] = {}  # small_idx -> big_idx
    for indices in buckets.values():
        if len(indices) < 2:
            continue
        # 大组优先（page 数多）
        sorted_idx = sorted(indices, key=lambda i: -len(files[i].pages))
        for k, big_idx in enumerate(sorted_idx):
            big = files[big_idx]
            if big_idx in merged_into:
                continue
            for small_idx in sorted_idx[k + 1:]:
                if small_idx in merged_into:
                    continue
                small = files[small_idx]
                if not _is_near_duplicate(big, small):
                    continue
                ratio = len(small.pages) / max(
                    1, len(big.pages) + len(small.pages),
                )
                if ratio > _NEAR_DUP_MAX_RATIO:
                    continue
                merged_into[small_idx] = big_idx

    if not merged_into:
        return files

    # 把 merged-in 的 pages 加到 big，重新构建 SourceFile
    for small_idx, big_idx in merged_into.items():
        big = files[big_idx]
        small = files[small_idx]
        big.pages.extend(small.pages)
        if "code.grouping.merged_near_duplicate" not in big.flags:
            big.flags.append("code.grouping.merged_near_duplicate")
        logger.debug(
            "near-dup merge: %r ← %r (%d pages)",
            big.filename, small.filename, len(small.pages),
        )

    # 重建合并后的 SourceFile（重新合 text、行号 gap、flags）
    rebuilt: list[SourceFile] = []
    for i, src in enumerate(files):
        if i in merged_into:
            continue
        if any(small_big[1] == i for small_big in merged_into.items()):
            # 大组：用最新 pages 重建
            rebuilt.append(_rebuild_source_file(src))
        else:
            rebuilt.append(src)
    return rebuilt


def _is_near_duplicate(big: SourceFile, small: SourceFile) -> bool:
    """两个 SourceFile 是否近重复：filename 编辑距离 ≤ 2 或一方是另一方后缀。"""
    big_name = big.filename.lower()
    small_name = small.filename.lower()
    if big_name == small_name:
        return True
    # suffix 关系：``_decode_accelerator.cc`` 是 ``widget_video_decode_
    # accelerator.cc`` 的真后缀（小组的 stem 长度 < 大组的 stem 长度）
    if (
        len(small_name) < len(big_name)
        and big_name.endswith(small_name)
    ):
        return True
    # 编辑距离 ≤ 2：page06873 ``acceleratorr.cc`` vs ``accelerator.cc``
    if abs(len(big_name) - len(small_name)) <= _NEAR_DUP_MAX_EDIT_DISTANCE:
        return _edit_distance_within(
            big_name, small_name, _NEAR_DUP_MAX_EDIT_DISTANCE,
        )
    return False


def _edit_distance_within(a: str, b: str, threshold: int) -> bool:
    """Levenshtein 距离是否 ≤ threshold（早停优化）。"""
    if abs(len(a) - len(b)) > threshold:
        return False
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for j, cb in enumerate(b, 1):
        curr = [j] + [0] * len(a)
        row_min = curr[0]
        for i, ca in enumerate(a, 1):
            curr[i] = (
                prev[i - 1] if ca == cb
                else 1 + min(prev[i - 1], prev[i], curr[i - 1])
            )
            row_min = min(row_min, curr[i])
        if row_min > threshold:
            return False
        prev = curr
    return prev[-1] <= threshold


def _rebuild_source_file(merged: SourceFile) -> SourceFile:
    """合并后用 merged.pages 重新计算 merged_text / line_no_range / flags。"""
    pages = merged.pages
    merged_text, line_no_range, gap_flags, provenance = (
        _merge_columns_by_line_no(pages, language=merged.language)
    )
    flags = [f for f in merged.flags if not f.startswith((
        "code.grouping.missing_line_nos=",
        "code.grouping.large_gap_collapsed",
        "code.merge.line_disagreement=",
    ))]
    flags.extend(gap_flags)
    sorted_pages = sorted(
        pages, key=lambda pc: _column_line_no_start(pc.column),
    )
    line_count = merged_text.count("\n") + 1 if merged_text else 0
    return SourceFile(
        path=merged.path,
        filename=merged.filename,
        language=merged.language,
        pages=sorted_pages,
        merged_text=merged_text,
        line_count=line_count,
        line_no_range=line_no_range,
        flags=flags,
        line_provenance=provenance,
    )


def _disambiguate_duplicate_paths(files: list[SourceFile]) -> None:
    """同 path 多 SourceFile 时加唯一后缀（in-place）。

    后缀以最小列号为基（``__col<idx>``），但仅列号不保证唯一——多个同 path
    文件的最小列号可能相同，会再次撞名被渲染层覆盖丢文件。故对已用路径递增序号
    直到全局唯一（B4 H3）。
    """
    used: set[str] = set()
    for src in files:
        if src.path not in used:
            used.add(src.path)
            continue
        col_indices = sorted({pc.column_index for pc in src.pages})
        base_suffix = f"__col{col_indices[0]}" if col_indices else "__dup"
        new_path, new_filename = _path_with_suffix(
            src.path, src.filename, base_suffix,
        )
        n = 1
        while new_path in used:
            n += 1
            new_path, new_filename = _path_with_suffix(
                src.path, src.filename, f"{base_suffix}_{n}",
            )
        used.add(new_path)
        src.path = new_path
        src.filename = new_filename
        src.flags.append("code.grouping.disambiguated_by_column")


def _path_with_suffix(
    path: str, filename: str, suffix: str,
) -> tuple[str, str]:
    """把后缀插到扩展名前：``foo.cc`` → ``foo<suffix>.cc``，并同步 path。"""
    if "." in filename:
        base, ext = filename.rsplit(".", 1)
        new_filename = f"{base}{suffix}.{ext}"
    else:
        new_filename = f"{filename}{suffix}"
    if "/" in path:
        head = path.rsplit("/", 1)[0]
        new_path = f"{head}/{new_filename}"
    else:
        new_path = new_filename
    return new_path, new_filename


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
    符）或 一方是另一方的后缀（如 ``core/widget`` ⊆ ``app/core/widget``）。
    """
    if len(group) <= 1:
        return [group]

    compacts = [_compact_dir(pc.meta.path) for pc in group]
    sub_groups: list[set[int]] = []
    for i, c1 in enumerate(compacts):
        placed = False
        # 单连接（与子组内任一成员兼容即可并入）：兼容关系非传递且空目录与
        # 任意目录兼容，理论上单张 path=filename（无 dir）的 PC 可把 ``a/x``
        # 与 ``b/x`` 两个不同目录桥接到一组。但实测主导回归是 OCR 把分隔符
        # ``/`` 误识为 ``7`` 等噪声把同源 dir 拆成多变体——若改全连接堵桥接，
        # ``media/gpu/openmax`` 与 ``media7gpu7openmax`` 的同名文件就会被
        # 拆 7 个孤立桶丢给下游 audit/repair 触发截断 + 编译失败，损害远大于
        # 边缘的 a/x↔b/x 误并风险。保持单连接，让空 dir 桥接吸收 OCR 噪声。
        for sg in sub_groups:
            if any(_dirs_compatible(c1, compacts[j]) for j in sg):
                sg.add(i)
                placed = True
                break
        if not placed:
            sub_groups.append({i})
    return [[group[i] for i in sg] for sg in sub_groups]


def _compact_dir(path: str | None) -> str:
    """从 ``app/core/widget/foo.cc`` 提 ``appcorewidget``（去 / 大小写不变）"""
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
    # canonical path/filename：run 级加权共识（分段投票 + 段内字符共识，
    # 观测唯一时 no-op；S3）。
    canonical_path, canonical_filename, name_confidence = (
        recover_canonical_path(group)
    )

    # canonical language：第一个非空
    language: str | None = None
    for pc in group:
        if pc.meta.language:
            language = pc.meta.language
            break

    # 按行号合并代码（language 用于决定大 gap 占位注释前缀）
    merged_text, line_no_range, gap_flags, provenance = (
        _merge_columns_by_line_no(group, language=language)
    )

    flags: list[str] = list(extra_flags or [])
    flags.extend(gap_flags)
    if len(group) > 1:
        flags.append(f"code.grouping.merged_pages={len(group)}")
        if any(_path_confidence(pc) < 0.6 for pc in group):
            flags.append("code.grouping.low_confidence_path_merged")
        if name_confidence < _NAME_CONSENSUS_LOW:
            flags.append("code.name.consensus_low")

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
        line_provenance=provenance,
    )


#: run 级命名共识：主选票权重占比低于此值标 code.name.consensus_low。
_NAME_CONSENSUS_LOW = 0.6
#: 分段投票时单段加权多数占比低于此值 → 放弃合成、退回最高权重观测整路径。
_SEGMENT_MIN_CONCENTRATION = 0.5


def recover_canonical_path(group: list[PageColumn]) -> tuple[str, str, float]:
    """run 级命名共识：从各页 path 观测恢复 canonical ``(path, filename, confidence)``。

    按 ``path_confidence`` 加权：
      - 观测唯一 → 直接返回（**no-op，防腐安全**：S1 归一后同 run 多为同一路径）。
      - 否则分段投票（从右逐段加权多数）重建路径，filename 段再做同长度变体的
        字符级共识；任一段多数占比 < 阈值则放弃合成、退回最高权重观测整路径。
    confidence = 各段多数占比的最小值（或退回时的整路径权重占比）。
    """
    obs: list[tuple[str, float]] = []
    for pc in group:
        candidate = pc.meta.path or pc.meta.filename
        if candidate:
            obs.append((candidate, _path_confidence(pc)))
    if not obs:
        return "_unknown", "_unknown", 0.0

    weight: dict[str, float] = {}
    for path, w in obs:
        weight[path] = weight.get(path, 0.0) + w
    if len(weight) == 1:
        only = next(iter(weight))
        return only, _filename_of_path(only), 1.0

    total = sum(weight.values())
    best_obs = max(weight, key=lambda p: weight[p])

    # filename：全体观测加权投票 + 同长度变体字符共识。
    filename, fn_conf = _weighted_vote(
        [(_filename_of_path(p), w) for p, w in obs],
    )
    filename = _filename_char_consensus(obs, filename)

    # dir：仅含 dir 的观测参与分段投票——dir-less 观测多是 OCR 漏识面包屑
    # 目录，不能让它把目录投没了（决策 2026-05-31）。
    dir_obs = [(p.rsplit("/", 1)[0], w) for p, w in obs if "/" in p]
    if dir_obs:
        dir_segments, dir_confs = _segment_vote(dir_obs)
        path = "/".join([*dir_segments, filename])
        confidence = min([fn_conf, *dir_confs]) if dir_confs else fn_conf
    else:
        path = filename
        confidence = fn_conf

    if confidence < _SEGMENT_MIN_CONCENTRATION:
        # 投票分散无明显多数 → 退回最高权重观测整路径（避免合成劣化）。
        return best_obs, _filename_of_path(best_obs), weight[best_obs] / total
    return path, filename, confidence


def _weighted_vote(items: list[tuple[str, float]]) -> tuple[str, float]:
    """加权多数投票，返回 ``(winner, 多数占比)``。"""
    agg: dict[str, float] = {}
    for value, w in items:
        agg[value] = agg.get(value, 0.0) + w
    total = sum(agg.values())
    winner = max(agg, key=lambda s: agg[s])
    return winner, (agg[winner] / total if total else 0.0)


def _filename_of_path(path: str) -> str:
    """full-path 的文件名段。"""
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _segment_vote(
    obs: list[tuple[str, float]],
) -> tuple[list[str], list[float]]:
    """从右对齐逐段加权多数投票，返回 ``(segments_left_to_right, 各段多数占比)``。"""
    pos_votes: dict[int, dict[str, float]] = {}
    depth_votes: dict[int, float] = {}
    for path, w in obs:
        segs = path.split("/")
        depth_votes[len(segs)] = depth_votes.get(len(segs), 0.0) + w
        for right_idx, seg in enumerate(reversed(segs)):
            bucket = pos_votes.setdefault(right_idx, {})
            bucket[seg] = bucket.get(seg, 0.0) + w
    # 权重并列时偏好段数更多者：OCR 只会漏识目录段（变短），不会凭空加段。
    depth = max(depth_votes, key=lambda d: (depth_votes[d], d))
    chosen_rev: list[str] = []
    concentrations: list[float] = []
    for right_idx in range(depth):
        votes = pos_votes.get(right_idx, {})
        if not votes:
            continue
        seg_total = sum(votes.values())
        winner = max(votes, key=lambda s: votes[s])
        chosen_rev.append(winner)
        concentrations.append(votes[winner] / seg_total)
    return list(reversed(chosen_rev)), concentrations


def _filename_char_consensus(
    obs: list[tuple[str, float]], voted_filename: str,
) -> str:
    """对与 voted_filename 等长的观测 filename 做逐位加权多数字符共识。

    修分散的单字符 OCR 噪声（不同页错在不同位置）；同长度变体 < 2 个则原样返回。
    """
    fn_weight: dict[str, float] = {}
    for path, w in obs:
        fn_weight[_filename_of_path(path)] = (
            fn_weight.get(_filename_of_path(path), 0.0) + w
        )
    length = len(voted_filename)
    same_len = {fn: w for fn, w in fn_weight.items() if len(fn) == length}
    if len(same_len) <= 1:
        return voted_filename
    chars: list[str] = []
    for i in range(length):
        char_votes: dict[str, float] = {}
        for fn, w in same_len.items():
            char_votes[fn[i]] = char_votes.get(fn[i], 0.0) + w
        chars.append(max(char_votes, key=lambda c: char_votes[c]))
    return "".join(chars)


def _path_confidence(pc: PageColumn) -> float:
    """PageColumn 当前路径的置信度，旧 fixture 缺字段时按中等可信处理。"""
    confidence = getattr(pc.meta, "path_confidence", 0.0)
    return float(confidence) if confidence > 0 else 0.65


def _column_line_no_start(column: CodeColumn) -> int:
    """CodeColumn 的起始行号（用于多张图排序）"""
    if not column.lines:
        return 0
    return min(line.line_no for line in column.lines)


def _column_line_no_end(column: CodeColumn) -> int:
    """CodeColumn 的结束行号。"""
    if not column.lines:
        return 0
    return max(line.line_no for line in column.lines)


#: 单次行号 gap 超过此阈值 → 不再批量塞空行，改插单行注释占位。
#: page06953/07002 错归案例里，错归 + 行号大跳跃产生过 587 个连续空行
#: 把文件膨胀到肉眼不可读。50 行是经验值：50 行内的 gap 多是 OCR 漏识
#: 或代码折叠，仍当作空白；超过就明显是结构性错误（错归 / 漏页），
#: 用注释明确标注，避免污染。
_GAP_FILL_THRESHOLD = 50

#: 大 gap 注释占位的语言适配。除"#"系语言外，统一用 // —— C/C++/Java/
#: JS/TS/Rust/Go/Swift/Kotlin/Scala/Dart/Groovy 等大多数曲线语言都接受。
#: 未识别语言（language=None）走 //。
_HASH_COMMENT_LANGUAGES: frozenset[str] = frozenset({
    "python", "shell", "ruby", "yaml", "toml", "perl", "r",
    "makefile", "dockerfile", "gn",
})


def _format_gap_marker(missing_count: int, language: str | None) -> str:
    """大 gap 占位注释，按语言选 # 或 // 前缀"""
    prefix = "# " if language in _HASH_COMMENT_LANGUAGES else "// "
    return f"{prefix}... ({missing_count} lines missing, see flags) ..."


def _merge_columns_by_line_no(
    group: list[PageColumn],
    *,
    language: str | None = None,
) -> tuple[str, tuple[int, int], list[str], dict[int, str]]:
    """按 line_no 合并多个 column，同行号多页取多数共识（S3）。

    替换旧「保留首次」：同一 line_no 多页给出不同文本时按多数投票（并列取
    最早出现），分歧计入 ``code.merge.line_disagreement``。同时产出 provenance
    （line_no -> 胜出页 stem）。行号 gap：小 gap（≤ ``_GAP_FILL_THRESHOLD``）填
    空行；大 gap 单行注释占位。``language`` 控制注释前缀。
    """
    # line_no -> [(渲染行, 来源页 stem)]，保留出现顺序
    observations: dict[int, list[tuple[str, str]]] = {}
    for pc in group:
        for line in pc.column.lines:
            rendered = " " * line.indent + line.text
            observations.setdefault(line.line_no, []).append(
                (rendered, pc.page_stem),
            )
    if not observations:
        return "", (0, 0), [], {}

    by_line_no: dict[int, str] = {}
    provenance: dict[int, str] = {}
    disagreements = 0
    for line_no, obs in observations.items():
        if len({text for text, _ in obs}) > 1:
            disagreements += 1
        text, stem = _consensus_line(obs)
        by_line_no[line_no] = text
        provenance[line_no] = stem

    sorted_nos = sorted(by_line_no)
    lo, hi = sorted_nos[0], sorted_nos[-1]

    flags: list[str] = []
    missing = set(range(lo, hi + 1)) - set(sorted_nos)
    if missing:
        flags.append(f"code.grouping.missing_line_nos={len(missing)}")
    if disagreements:
        flags.append(f"code.merge.line_disagreement={disagreements}")

    merged_text, saw_large_gap = _render_with_gaps(
        by_line_no, sorted_nos, language,
    )
    if saw_large_gap:
        flags.append("code.grouping.large_gap_collapsed")
    return merged_text, (lo, hi), flags, provenance


def _render_with_gaps(
    by_line_no: dict[int, str],
    sorted_nos: list[int],
    language: str | None,
) -> tuple[str, bool]:
    """按行号拼接，小 gap 填空行、大 gap 单行注释占位。返回 ``(文本, 见过大gap)``。"""
    parts: list[str] = []
    prev_no = sorted_nos[0] - 1
    saw_large_gap = False
    for no in sorted_nos:
        gap = no - prev_no - 1
        if gap > _GAP_FILL_THRESHOLD:
            parts.append(_format_gap_marker(gap, language))
            saw_large_gap = True
        elif gap > 0:
            parts.extend([""] * gap)
        parts.append(by_line_no[no])
        prev_no = no
    return "\n".join(parts), saw_large_gap


def _consensus_line(obs: list[tuple[str, str]]) -> tuple[str, str]:
    """同行号多页观测取多数共识：最高频文本，并列取最早出现。

    返回 ``(胜出文本, 该文本最早出现页 stem)``。
    """
    counts = Counter(text for text, _ in obs)
    first_index: dict[str, int] = {}
    for i, (text, _) in enumerate(obs):
        first_index.setdefault(text, i)
    winner = min(counts, key=lambda t: (-counts[t], first_index[t]))
    stem = next(stem for text, stem in obs if text == winner)
    return winner, stem
