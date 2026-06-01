# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""代码模式 Stage 1：批量文件名/路径归一（AGE-80）

跨全 batch 把低置信 / 少数派的噪声 path 碎片 **snap 回** 高置信权威值，让
S2 按文件名分桶时不被 OCR 字符噪声拆散。基于统计事实：正确名字在整批里
高频反复出现，而每种 OCR 错读零星、置信度低。

三步：
  1. ``build_vocabulary``：按 ``path_confidence`` 加权统计每个 full-path 的
     支持度，支持度 ≥ τ 或频次 ≥ k 的进**权威集**。
  2. ``build_canonical_map``：把权威集里互为近重复的 path 归到同一簇，簇的
     canonical = 支持度最高者（传递解析，解决「typo+虚假目录段」同时出现时
     落到中间变体的问题）。
  3. ``reconcile_paths``：①自身是权威但非 canonical 的少数派变体 → 直接 snap
     到 canonical；②非权威碎片 → 找最近权威目标、经 canonical 解析后 snap。
     就地改写 ``meta.path/filename``，标 ``code.meta.snapped_to_vocab`` 并把原值
     记进 ``path_candidates``（可溯源）。等距多邻 → ``code.meta.snap_ambiguous``。
     无近邻的 garbage（如 ``giesz.cc``）保持原样，留 S2 行号裁决。

保守约束（S1 在 S2 之前落地，需自我设限防误并）：
  - **同扩展名硬约束**：``.h`` 与 ``.cc`` 永不合；``.c``↔``.cc`` 这类扩展名
    OCR 漏字符交 S2 行号救援，S1 不跨扩展名。
  - **少数派守门**：仅当碎片支持度 ≤ ``minority_ratio`` × 目标支持度才 snap，
    避免把两个体量相当的同名近邻文件合并（那种交 S2 内容裁决）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from docrestore.processing.code_file_grouping import PageColumn
from docrestore.processing.ide_meta_extract import IDEMeta, PathCandidate

#: snap 后回填的 path 置信度（表示已对齐权威值）。
_SNAPPED_CONFIDENCE = 0.85


@dataclass(frozen=True)
class ReconcileConfig:
    """Stage 1 阈值（pipeline 从 ``CodeRestoreConfig`` 映射传入）。"""

    support_threshold: float = 1.5
    min_frequency: int = 3
    filename_max_distance: int = 1  # 距离 2 会误并真实近名文件，交 S2 裁决
    dir_max_distance: int = 2
    minority_ratio: float = 0.5


@dataclass
class PathVocabulary:
    """全 batch 的路径词表。"""

    paths: dict[str, float] = field(default_factory=dict)   # full-path -> 加权支持度
    path_freq: dict[str, int] = field(default_factory=dict)  # full-path -> 频次
    dirs: dict[str, float] = field(default_factory=dict)     # dir -> 支持度
    filenames: dict[str, float] = field(default_factory=dict)  # filename -> 支持度
    authoritative: set[str] = field(default_factory=set)     # 进权威集的 full-path


def build_vocabulary(
    metas: list[IDEMeta], config: ReconcileConfig | None = None,
) -> PathVocabulary:
    """按 ``path_confidence`` 加权统计 path/dir/filename 支持度并圈定权威集。"""
    cfg = config or ReconcileConfig()
    vocab = PathVocabulary()
    freq: Counter[str] = Counter()
    for meta in metas:
        if not meta.path or not meta.filename:
            continue
        conf = max(0.0, meta.path_confidence)
        vocab.paths[meta.path] = vocab.paths.get(meta.path, 0.0) + conf
        freq[meta.path] += 1
        directory = _dir_of(meta.path)
        if directory:
            vocab.dirs[directory] = vocab.dirs.get(directory, 0.0) + conf
        vocab.filenames[meta.filename] = (
            vocab.filenames.get(meta.filename, 0.0) + conf
        )
    vocab.path_freq = dict(freq)
    vocab.authoritative = {
        path for path, support in vocab.paths.items()
        if support >= cfg.support_threshold
        or freq[path] >= cfg.min_frequency
    }
    return vocab


def build_canonical_map(
    vocab: PathVocabulary, config: ReconcileConfig | None = None,
) -> dict[str, str]:
    """权威 path -> 其近重复簇 canonical（最高支持度，传递解析）。"""
    cfg = config or ReconcileConfig()
    auth = vocab.authoritative
    parent: dict[str, str] = {}
    for path in auth:
        own_support = vocab.paths.get(path, 0.0)
        candidates = _candidates_for(path, own_support, auth, vocab, cfg)
        if not candidates:
            continue
        target, ambiguous = _select_target(candidates)
        if target is not None and not ambiguous:
            parent[path] = target

    canonical: dict[str, str] = {}
    for path in auth:
        seen: set[str] = set()
        cur = path
        while cur in parent and cur not in seen:
            seen.add(cur)
            cur = parent[cur]
        canonical[path] = cur
    return canonical


def reconcile_paths(
    pcs: list[PageColumn],
    vocab: PathVocabulary,
    config: ReconcileConfig | None = None,
) -> None:
    """就地把噪声 path 碎片 snap 回权威值；无权威集时直接返回（不抛）。"""
    cfg = config or ReconcileConfig()
    if not vocab.authoritative:
        return
    canonical = build_canonical_map(vocab, cfg)
    for pc in pcs:
        meta = pc.meta
        if not meta.path or not meta.filename:
            continue
        # ① 自身是权威但非 canonical 的少数派变体 → 直接归并到 canonical。
        own_canonical = canonical.get(meta.path, meta.path)
        if own_canonical != meta.path:
            _apply_snap(meta, own_canonical)
            continue
        if meta.path in vocab.authoritative:
            continue  # 自身即 canonical 权威 → 保留
        # ② 非权威碎片 → 找最近权威目标，经 canonical 解析后 snap。
        own_support = vocab.paths.get(meta.path, 0.0)
        candidates = _candidates_for(
            meta.path, own_support, vocab.authoritative, vocab, cfg,
        )
        if not candidates:
            continue
        target, ambiguous = _select_target(candidates)
        if ambiguous:
            _append_flag(meta, "code.meta.snap_ambiguous")
            continue
        if target is not None:
            _apply_snap(meta, canonical.get(target, target))


def _candidates_for(
    path: str,
    own_support: float,
    targets: set[str],
    vocab: PathVocabulary,
    cfg: ReconcileConfig,
) -> list[tuple[int, str]]:
    """返回 ``(combined_distance, target_path)`` 候选列表。

    过滤：同扩展名、stem 编辑距离 ≤ D、dir 兼容、目标支持度严格更高且本路径
    为少数派（own ≤ minority_ratio × target）。
    """
    own_stem, own_ext = _split_ext(_filename_of(path))
    own_dir = _compact_dir(path)
    out: list[tuple[int, str]] = []
    for target in targets:
        if target == path:
            continue
        target_support = vocab.paths.get(target, 0.0)
        if target_support <= own_support:
            continue
        if own_support > cfg.minority_ratio * target_support:
            continue  # 体量相当 → 不在 S1 合并，交 S2
        target_stem, target_ext = _split_ext(_filename_of(target))
        if target_ext != own_ext:
            continue  # 同扩展名硬约束
        stem_dist = _edit_distance(own_stem, target_stem)
        if stem_dist > cfg.filename_max_distance:
            continue
        dir_dist = _dir_distance(
            own_dir, _compact_dir(target), cfg.dir_max_distance,
        )
        if dir_dist is None:
            continue
        out.append((stem_dist + dir_dist, target))
    return out


def _select_target(
    candidates: list[tuple[int, str]],
) -> tuple[str | None, bool]:
    """选最近邻；最小距离上有多个不同路径并列 → 判 ambiguous。"""
    candidates.sort(key=lambda item: item[0])
    best_dist = candidates[0][0]
    tied = {path for dist, path in candidates if dist == best_dist}
    if len(tied) > 1:
        return None, True
    return candidates[0][1], False


def _apply_snap(meta: IDEMeta, target_path: str) -> None:
    """就地把 meta 改写为 target_path，记录原值到 path_candidates。"""
    original = meta.path or ""
    target_file = _filename_of(target_path)
    meta.path_candidates.append(PathCandidate(
        path=target_path,
        filename=target_file,
        language=meta.language,
        source="vocab",
        confidence=_SNAPPED_CONFIDENCE,
        raw_text=original,
        flags=["code.meta.snapped_to_vocab"],
    ))
    meta.path = target_path
    meta.filename = target_file
    meta.path_confidence = max(meta.path_confidence, _SNAPPED_CONFIDENCE)
    _append_flag(meta, "code.meta.snapped_to_vocab")


def _append_flag(meta: IDEMeta, flag: str) -> None:
    if flag not in meta.flags:
        meta.flags.append(flag)


def _dir_of(path: str) -> str:
    """full-path 的目录部分（无目录返回空串）。"""
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _filename_of(path: str) -> str:
    """full-path 的文件名部分。"""
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _compact_dir(path: str | None) -> str:
    """目录去掉所有 ``/`` 的紧凑串（OCR 漏分隔符容错用）。"""
    if not path:
        return ""
    return _dir_of(path).replace("/", "")


def _split_ext(filename: str) -> tuple[str, str]:
    """拆 ``(stem, ext)``；无扩展名时 ext 为空串。"""
    if "." not in filename:
        return filename, ""
    stem, ext = filename.rsplit(".", 1)
    return stem, ext.lower()


def _dir_distance(
    own: str, target: str, max_distance: int,
) -> int | None:
    """compact dir 距离：相等/空/互为后缀记 0，否则编辑距离（超限返回 None）。"""
    if own == target or not own or not target:
        return 0
    if own.endswith(target) or target.endswith(own):
        return 0
    dist = _edit_distance(own, target)
    return dist if dist <= max_distance else None


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein 编辑距离（小串，直接 DP）。"""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    prev = list(range(len(right) + 1))
    for i, lch in enumerate(left, 1):
        curr = [i] + [0] * len(right)
        for j, rch in enumerate(right, 1):
            curr[j] = (
                prev[j - 1] if lch == rch
                else 1 + min(prev[j - 1], prev[j], curr[j - 1])
            )
        prev = curr
    return prev[-1]
