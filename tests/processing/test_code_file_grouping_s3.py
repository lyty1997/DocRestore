# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""S3 共识合并 + run 级命名共识 + provenance 单测（AGE-82）

覆盖：同行号多页多数共识 + line_disagreement + line_provenance；run 级命名
分段投票灭少数派 dir、dir 仅由含 dir 观测投票（不被 dir-less 投没）、filename
字符级共识修分散单字符噪声、uniform no-op、低置信 consensus_low。

测试数据用通用占位内容，不写死数据集标识符（CLAUDE.md 测试规则）。
"""

from __future__ import annotations

from docrestore.processing.code_assembly import CodeColumn, CodeLine
from docrestore.processing.code_file_grouping import (
    GroupingConfig,
    PageColumn,
    group_into_files,
    recover_canonical_path,
)
from docrestore.processing.ide_meta_extract import IDEMeta


def _pc(
    path: str, *, conf: float = 0.95, stem: str = "p", col: int = 0,
    filename: str | None = None,
) -> PageColumn:
    fname = filename if filename is not None else (
        path.rsplit("/", 1)[-1] if "/" in path else path
    )
    meta = IDEMeta(
        column_index=col, filename=fname, path=path,
        language="cpp", path_confidence=conf,
    )
    column = CodeColumn(
        column_index=col, bbox=(0, 0, 0, 0), code_text="",
        lines=[], char_width=1.0, avg_line_height=1,
    )
    return PageColumn(page_stem=stem, column_index=col, meta=meta, column=column)


def _pcl(stem: str, path: str, line_texts: dict[int, str]) -> PageColumn:
    """带 CodeLine 的 PageColumn（用于合并共识测试）。"""
    fname = path.rsplit("/", 1)[-1] if "/" in path else path
    meta = IDEMeta(
        column_index=0, filename=fname, path=path,
        language="cpp", path_confidence=0.95,
    )
    ordered = sorted(line_texts.items())
    lines = [CodeLine(line_no=n, text=t, indent=0, bbox=None) for n, t in ordered]
    column = CodeColumn(
        column_index=0, bbox=(0, 0, 0, 0),
        code_text="\n".join(t for _, t in ordered),
        lines=lines, char_width=1.0, avg_line_height=1,
    )
    return PageColumn(page_stem=stem, column_index=0, meta=meta, column=column)


# ---------- 共识合并 + provenance ----------

def test_consensus_merge_majority_and_provenance() -> None:
    """同行号多页分歧 → 多数文本胜出 + line_disagreement + provenance 溯源。"""
    pcs = [
        _pcl("p", "a/f.cc", {1: "x = 1", 5: "y = compute()"}),
        _pcl("q", "a/f.cc", {5: "y = compute()", 9: "z = 2"}),
        _pcl("r", "a/f.cc", {5: "y = c0mpute()"}),  # 少数派 OCR 噪声
    ]
    sources = group_into_files(pcs)
    assert len(sources) == 1
    src = sources[0]
    assert "y = compute()" in src.merged_text   # 多数胜出
    assert "y = c0mpute()" not in src.merged_text
    assert any(f.startswith("code.merge.line_disagreement=") for f in src.flags)
    assert src.line_provenance[5] in {"p", "q"}  # 记录胜出页
    assert src.line_provenance[1] == "p"


# ---------- run 级命名共识 ----------

def test_recover_uniform_is_noop() -> None:
    """观测唯一 → 直接返回（防腐安全）。"""
    pcs = [_pc("a/b/f.cc", stem="p"), _pc("a/b/f.cc", stem="q")]
    path, filename, conf = recover_canonical_path(pcs)
    assert (path, filename) == ("a/b/f.cc", "f.cc")
    assert conf == 1.0


def test_recover_segment_vote_drops_minority_dir() -> None:
    """少数派 dir（ui/g）被多数（ui/gl）投票淘汰。"""
    pcs = [
        _pc("ui/gl/foo.cc", stem="a"),
        _pc("ui/gl/foo.cc", stem="b"),
        _pc("ui/gl/foo.cc", stem="c"),
        _pc("ui/g/foo.cc", stem="d"),
    ]
    path, _filename, _conf = recover_canonical_path(pcs)
    assert path == "ui/gl/foo.cc"


def test_recover_dir_only_from_dir_bearing_observations() -> None:
    """dir-less 观测（OCR 漏识面包屑目录）不能把目录投没。"""
    pcs = [
        _pc("media/gpu/x.cc", stem="a"),
        _pc("media/gpu/x.cc", stem="b"),
        _pc("x.cc", stem="c"),  # 漏 dir
    ]
    path, _filename, _conf = recover_canonical_path(pcs)
    assert path == "media/gpu/x.cc"  # 目录保住


def test_recover_filename_char_consensus() -> None:
    """无单一多数 filename，但逐位字符共识可拼出正确名（修分散单字符噪声）。"""
    pcs = [
        _pc("a/render.cc", stem="p"),
        _pc("a/rxnder.cc", stem="q"),  # pos1 噪声
        _pc("a/rendxr.cc", stem="r"),  # pos4 噪声
    ]
    path, filename, _conf = recover_canonical_path(pcs)
    assert filename == "render.cc"
    assert path == "a/render.cc"


def test_recover_more_complete_path_wins_on_tie() -> None:
    """段数并列偏好更完整路径（core/widget vs app/core/widget）。"""
    pcs = [
        _pc("core/widget/BUILD.gn", stem="a", conf=0.9),
        _pc("app/core/widget/BUILD.gn", stem="b", conf=0.9),
    ]
    path, _filename, _conf = recover_canonical_path(pcs)
    assert path == "app/core/widget/BUILD.gn"


def test_recover_low_confidence_split() -> None:
    """filename 严重分裂 → 置信度低（供上层标 consensus_low）。"""
    pcs = [_pc("a/alpha.cc", stem="p"), _pc("a/bravo.cc", stem="q")]
    _path, _filename, conf = recover_canonical_path(pcs)
    assert conf < 0.6


def test_consensus_low_flag_on_split_group() -> None:
    """同 fuzzy-key 但 filename 分裂的组 → 文件标 code.name.consensus_low。"""
    # lib.cc / 1ib.cc 视觉混淆 → 同 fuzzy key 进同组，但 filename 投票 50/50
    pcs = [_pc("a/lib.cc", stem="p"), _pc("a/1ib.cc", stem="q")]
    sources = group_into_files(pcs, {}, GroupingConfig())
    assert len(sources) == 1
    assert "code.name.consensus_low" in sources[0].flags
