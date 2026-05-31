# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stage 1 批量文件名/路径归一单测（AGE-80）

覆盖：词表权威集判定 / dir 漏字符 snap / 虚假目录段 snap / 双下划线 snap /
garbage 不改 / 跨扩展名不合 / 少数派守门 / 等距 ambiguous / 空词表 no-op /
dominant 不动。

测试路径全部用通用占位名，不写死任何数据集标识符（CLAUDE.md 测试规则）。
"""

from __future__ import annotations

from docrestore.processing.code_assembly import CodeColumn
from docrestore.processing.code_file_grouping import PageColumn
from docrestore.processing.code_path_reconcile import (
    ReconcileConfig,
    build_vocabulary,
    reconcile_paths,
)
from docrestore.processing.ide_meta_extract import IDEMeta


def _pc(
    path: str,
    *,
    conf: float = 0.95,
    stem: str = "p",
    col: int = 0,
    lang: str = "cpp",
) -> PageColumn:
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    meta = IDEMeta(
        column_index=col,
        filename=filename,
        path=path,
        language=lang,
        path_confidence=conf,
    )
    column = CodeColumn(
        column_index=col, bbox=(0, 0, 0, 0), code_text="",
        lines=[], char_width=1.0, avg_line_height=1,
    )
    return PageColumn(page_stem=stem, column_index=col, meta=meta, column=column)


def _many(path: str, count: int, *, conf: float = 0.95) -> list[PageColumn]:
    return [_pc(path, conf=conf, stem=f"d{i}") for i in range(count)]


def _reconcile(
    pcs: list[PageColumn], config: ReconcileConfig | None = None,
) -> None:
    cfg = config or ReconcileConfig()
    vocab = build_vocabulary([pc.meta for pc in pcs], cfg)
    reconcile_paths(pcs, vocab, cfg)


def test_vocabulary_authoritative_by_freq_or_support() -> None:
    """频次 ≥ k 或加权支持度 ≥ τ 进权威集；零星低置信不进。"""
    metas = [
        *[pc.meta for pc in _many("app/core/a.cc", 4)],   # 频次 4 → 权威
        _pc("app/core/lonely.cc", conf=0.4).meta,         # 频次 1 + 低置信
    ]
    vocab = build_vocabulary(metas)
    assert "app/core/a.cc" in vocab.authoritative
    assert "app/core/lonely.cc" not in vocab.authoritative
    assert vocab.paths["app/core/a.cc"] == round(4 * 0.95, 10)


def test_snap_dir_missing_char() -> None:
    """目录漏一个字符（cor vs core）→ snap 回权威 dir。"""
    frag = _pc("app/cor/widget.cc", conf=0.48, stem="frag")
    pcs = [frag, *_many("app/core/widget.cc", 5)]
    _reconcile(pcs)
    assert frag.meta.path == "app/core/widget.cc"
    assert "code.meta.snapped_to_vocab" in frag.meta.flags
    # 原值留痕到 path_candidates（可溯源）
    assert any(c.source == "vocab" and c.raw_text == "app/cor/widget.cc"
               for c in frag.meta.path_candidates)


def test_snap_spurious_directory_segment() -> None:
    """虚假单字符目录段（图标 C 误识成 c/）→ 被淘汰，snap 回真路径。"""
    frag = _pc("app/core/c/header.h", conf=0.95, stem="frag")
    pcs = [frag, *_many("app/core/header.h", 6)]
    _reconcile(pcs)
    assert frag.meta.path == "app/core/header.h"
    assert "code.meta.snapped_to_vocab" in frag.meta.flags


def test_snap_double_underscore_filename() -> None:
    """文件名多一个下划线（少数派，体量约 1/4）→ snap 回主名。"""
    variant = [_pc("a/b/render__view.h", stem="v0"),
               _pc("a/b/render__view.h", stem="v1")]
    pcs = [*variant, *_many("a/b/render_view.h", 6)]
    _reconcile(pcs)
    for pc in variant:
        assert pc.meta.path == "a/b/render_view.h"


def test_garbage_filename_not_snapped() -> None:
    """与任何权威名都差太远的 garbage（如 OCR 乱码标题）→ 保持原样交 S2。"""
    frag = _pc("ui/gl/giesz.cc", conf=0.48, stem="frag")
    pcs = [frag, *_many("ui/gl/surface_render_target.cc", 6)]
    _reconcile(pcs)
    assert frag.meta.path == "ui/gl/giesz.cc"
    assert "code.meta.snapped_to_vocab" not in frag.meta.flags


def test_cross_extension_never_snapped() -> None:
    """同 stem 不同扩展名永不合：.h↔.cc、.c↔.cc 都交 S2。"""
    header = _pc("a/foo.h", conf=0.48, stem="h")
    csrc = _pc("a/foo.c", conf=0.48, stem="c")
    pcs = [header, csrc, *_many("a/foo.cc", 6)]
    _reconcile(pcs)
    assert header.meta.path == "a/foo.h"   # .h 不并进 .cc
    assert csrc.meta.path == "a/foo.c"     # .c 漏字符交 S2 行号救援


def test_distance_two_distinct_files_not_merged() -> None:
    """真实近名文件差 2 字符（x11 vs x11xv，插入 "xv"）→ 绝不合并，交 S2。

    回归实测 bug：filename_max_distance=2 时 14 页的 ..._x11_gles2.cc 被误并进
    39 页的 ..._x11xv_gles2.cc。stem 距离 2 是真实文件差异而非 OCR 噪声。
    """
    x11 = _many("ui/gl/surface_x11_gles2.cc", 14, conf=0.95)
    x11xv = _many("ui/gl/surface_x11xv_gles2.cc", 39, conf=0.72)
    pcs = [*x11, *x11xv]
    _reconcile(pcs)
    assert all(pc.meta.path == "ui/gl/surface_x11_gles2.cc" for pc in x11)
    assert all(pc.meta.path == "ui/gl/surface_x11xv_gles2.cc" for pc in x11xv)


def test_minority_ratio_guards_comparable_files() -> None:
    """体量相当的同名近邻文件（foo / foo2 各占一半）→ S1 不合并，交 S2。"""
    a = _many("a/foo.cc", 3)
    b = _many("a/foo2.cc", 3)
    pcs = [*a, *b]
    _reconcile(pcs)
    assert all(pc.meta.path == "a/foo.cc" for pc in a)
    assert all(pc.meta.path == "a/foo2.cc" for pc in b)


def test_equidistant_targets_marked_ambiguous() -> None:
    """少数派碎片到两个等距权威名 → 标 ambiguous，不擅自改。"""
    frag = _pc("a/foo.cc", conf=0.4, stem="frag")
    pcs = [frag, *_many("a/fooa.cc", 6), *_many("a/foob.cc", 6)]
    _reconcile(pcs)
    assert frag.meta.path == "a/foo.cc"
    assert "code.meta.snap_ambiguous" in frag.meta.flags
    assert "code.meta.snapped_to_vocab" not in frag.meta.flags


def test_empty_vocabulary_is_noop() -> None:
    """全是零星低置信项 → 权威集为空 → reconcile 不改任何路径，不抛。"""
    pcs = [
        _pc("x/one.cc", conf=0.3, stem="a"),
        _pc("y/two.cc", conf=0.3, stem="b"),
    ]
    _reconcile(pcs)
    assert pcs[0].meta.path == "x/one.cc"
    assert pcs[1].meta.path == "y/two.cc"
    assert pcs[0].meta.flags == []


def test_transitive_canonical_resolution() -> None:
    """typo + 虚假目录段同时出现的碎片 → 经 canonical 传递解析落到根，不卡中间变体。

    a/b/c/headerr.h（typo 多 r + 虚假 c/）应越过中间的 a/b/c/header.h
    （7 页，自身也是 a/b/header.h 的少数派变体）直达 a/b/header.h（主名）。
    """
    root = _many("a/b/header.h", 8)
    mid = _many("a/b/c/header.h", 3)          # 虚假 c/ 的少数派权威变体
    frag = _pc("a/b/c/headerr.h", conf=0.48, stem="frag")
    pcs = [*root, *mid, frag]
    _reconcile(pcs)
    assert all(pc.meta.path == "a/b/header.h" for pc in mid)
    assert frag.meta.path == "a/b/header.h"   # 传递解析到根，不停在 c/header.h


def test_dominant_path_unchanged() -> None:
    """支持度最高的 canonical 自身没有更高目标 → 保持不变。"""
    dom = _many("a/b/main.cc", 5)
    _reconcile(dom)
    assert all(pc.meta.path == "a/b/main.cc" for pc in dom)
    assert all("code.meta.snapped_to_vocab" not in pc.meta.flags for pc in dom)
