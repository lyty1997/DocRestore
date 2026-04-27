# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IDE 元数据提取单测（AGE-8 Phase 2.2）"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docrestore.models import TextLine
from docrestore.processing.ide_layout import IDELayout, analyze_layout
from docrestore.processing.ide_meta_extract import (
    IDEMeta,
    extract_ide_metas,
)


def _line(
    bbox: tuple[int, int, int, int], text: str, score: float = 0.95,
) -> TextLine:
    return TextLine(bbox=bbox, text=text, score=score)


def _make_layout_with_above(
    above_lines: list[TextLine],
    *,
    anchor_count: int = 1,
) -> IDELayout:
    """构造一个含指定 above_code 的 layout fixture。

    anchor 占位：让 above_code 的 line 落在 column 范围内即可。
    单 anchor x=200，双 anchor x=200 + x=2000。
    """
    lines: list[TextLine] = []
    if anchor_count == 1:
        anchor_x_centers = [200]
    else:
        anchor_x_centers = [200, 2000]

    # 行号占位：每个 anchor 5 行，y 从 1000 开始（above_code 在 < 1000）
    for ax in anchor_x_centers:
        for i in range(1, 6):
            y = 1000 + i * 30
            lines.append(_line((ax, y, ax + 30, y + 25), str(i)))
            # 代码占位避免空 column
            lines.append(_line(
                (ax + 100, y, ax + 800, y + 25), f"code{i}",
            ))

    # 加 above_code（y < 1000 让 ide_layout 归到 above）
    lines.extend(above_lines)
    return analyze_layout(lines, image_size=(3000, 2000))


# ---------- 单元测试 ----------

class TestSingleColumn:
    def test_breadcrumb_basic(self) -> None:
        above = [_line(
            (100, 100, 1000, 130),
            "media > gpu > openmax > foo.cc",
        )]
        layout = _make_layout_with_above(above, anchor_count=1)
        metas = extract_ide_metas(layout)
        assert len(metas) == 1
        m = metas[0]
        assert m.filename == "foo.cc"
        assert m.path == "media/gpu/openmax/foo.cc"
        assert m.language == "cpp"
        assert m.breadcrumb_readable is True
        assert m.tab_readable is True

    def test_breadcrumb_with_icon_prefix(self) -> None:
        """OCR 把 VSCode 文件图标识别成 'C ' 前缀，应清洗"""
        above = [_line(
            (100, 100, 1000, 130),
            "media >gpu >openmax > C openmax_status.h",
        )]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "openmax_status.h"
        assert m.path == "media/gpu/openmax/openmax_status.h"
        assert m.language == "cpp"

    def test_breadcrumb_with_symbol_path(self) -> None:
        """breadcrumb 末尾跟 symbol path（如 `> {}media > Symbol`），应忽略"""
        above = [_line(
            (100, 100, 1500, 130),
            "media >gpu >openmax > C+ foo.cc > {}media > AllocateOmxC",
        )]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "foo.cc"
        assert m.path == "media/gpu/openmax/foo.cc"

    def test_tab_only_no_breadcrumb(self) -> None:
        """无 breadcrumb 时，tab fallback 拿到 filename（path=None）"""
        above = [_line((100, 100, 600, 130), "C+ foo.cc 4×")]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "foo.cc"
        assert m.path is None
        assert m.language == "cpp"
        assert "code.breadcrumb_missing" in m.flags
        # 审计信号：本栏 filename 来自 tab 兜底
        assert "code.tab_only_fallback" in m.flags

    def test_breadcrumb_path_missing_flag_only_when_breadcrumb_wins(
        self,
    ) -> None:
        """breadcrumb 解出 filename 但缺 path → breadcrumb_path_missing 才标。

        反例：breadcrumb 啥都没解出、tab 兜底拿 filename 而 path 为 None
        时不应错标 breadcrumb_path_missing（那是 breadcrumb 自己的问题）。
        """
        above = [_line((100, 100, 600, 130), "C+ foo.cc 4×")]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        # tab 兜底场景不应出现 breadcrumb_path_missing
        assert "code.breadcrumb_path_missing" not in m.flags

    def test_no_tab_no_breadcrumb(self) -> None:
        """两者都缺 → tab_unreadable flag"""
        above = [_line((100, 100, 600, 130), "Code File Edit")]  # menu only
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename is None
        assert m.path is None
        assert m.tab_readable is False
        assert "code.tab_unreadable" in m.flags

    def test_truncated_breadcrumb_recovered_by_tab(self) -> None:
        """breadcrumb 末段被截（无扩展名），tab 含完整文件名 → 综合恢复"""
        above = [
            _line(
                (100, 100, 1500, 130),
                "media >gpu >openmax >C+ openmax_video_decode_ac",
            ),
            _line(
                (100, 60, 800, 90),
                "C+ openmax_video_decode_accelerator.cc 4×",
            ),
        ]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        # breadcrumb 末段含 `_ac` 没扩展名 → fallback 到 tab
        assert m.filename == "openmax_video_decode_accelerator.cc"

    def test_breadcrumb_wins_over_tab_with_different_extension(
        self,
    ) -> None:
        """回归 DSC06953/07002：IDE 多 tab + breadcrumb 指向 .h，
        tab bar 同时显示 .cc 和 .h（split editor 共享 tab bar）。
        breadcrumb 是当前打开的源文件，扩展名不能被 tab 翻覆。
        """
        above = [
            # breadcrumb：当前栏打开的是 .h
            _line(
                (100, 100, 1500, 130),
                "media >gpu >openmax > C openmax_video_decode_accelerator.h",
            ),
            # tab bar：同时显示多个 tab，包含别的扩展名
            _line(
                (100, 60, 800, 90),
                "C+ gles2_dmabuf_to_egl_image_translator.cc 2×",
            ),
            _line(
                (820, 60, 1500, 90),
                "C+ openmax_video_decode_accelerator.cc 4×",
            ),
        ]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        # 必须取 breadcrumb 的 .h，不能被 tab 的 .cc 覆盖
        assert m.filename == "openmax_video_decode_accelerator.h"
        assert m.path == (
            "media/gpu/openmax/openmax_video_decode_accelerator.h"
        )

    def test_breadcrumb_wins_over_tab_completely_different_file(
        self,
    ) -> None:
        """breadcrumb 给的文件名和 tab 完全不同 → 仍以 breadcrumb 为准。

        IDE 顶部 tab bar 横跨整个窗口，多 tab 时 OCR 容易把 inactive tab
        当 active 命中（例如未保存修改数 ``cc 2`` 被识为 ``cc 2×``）。
        """
        above = [
            _line(
                (100, 100, 1500, 130),
                "media >gpu >openmax > foo.h",
            ),
            _line((100, 60, 800, 90), "C+ bar.cc 4×"),
        ]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "foo.h"
        assert m.path == "media/gpu/openmax/foo.h"

    def test_breadcrumb_fragments_stitched_with_overlap_dedup(self) -> None:
        """回归 DSC06953：breadcrumb OCR 拆成多 bbox + 边界字符共享。

        ``openmax_`` (x=611-769) + ``_video_decode_accelerator.cc`` (x=754-
        1216) 在 x 重叠区共享 ``_``。stitch 后应去重，得到完整 filename，
        不应因截断错归到 tab 里别的 .cc 文件。
        """
        above = [
            # 同 y-band 的 breadcrumb 片段
            _line((164, 145, 534, 191), "media >gpu >openmax"),
            _line((569, 146, 607, 177), "C+"),
            _line((611, 147, 769, 182), "openmax_"),
            _line((754, 135, 1216, 180), "_video_decode_accelerator.cc"),
            # tab bar：同时显示多个 .cc tab，无 active 标记
            _line(
                (920, 57, 1634, 107),
                "C+gles2_dmabuf_to_egl_image_translator.cc 2",
            ),
            _line(
                (161, 66, 826, 117),
                "C+ openmax_video_decode_accelerator.cc 4",
            ),
        ]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "openmax_video_decode_accelerator.cc"
        assert m.path == (
            "media/gpu/openmax/openmax_video_decode_accelerator.cc"
        )

    def test_breadcrumb_truncated_filename_completed_via_tab(self) -> None:
        """回归 DSC07050：stitched breadcrumb 含 ``_decode_accelerator.cc``
        （前缀被覆盖丢失），用同栏 tab 候选 suffix-match 补全完整名。
        """
        above = [
            # breadcrumb 解出来 filename 是 ``_decode_accelerator.cc`` 截断版
            _line(
                (100, 100, 1500, 130),
                "media > gpu > openmax > _decode_accelerator.cc",
            ),
            # tab bar：包含完整 openmax_video_decode_accelerator.cc，无 ×
            _line(
                (920, 57, 1634, 107),
                "C+gles2_dmabuf_to_egl_image_translator.cc 2",
            ),
            _line(
                (161, 66, 826, 117),
                "C+ openmax_video_decode_accelerator.cc 4",
            ),
        ]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "openmax_video_decode_accelerator.cc"
        assert m.path == (
            "media/gpu/openmax/openmax_video_decode_accelerator.cc"
        )

    def test_breadcrumb_path_segment_merged_with_filename(self) -> None:
        """回归 DSC07050：OCR 漏 ``>`` 导致 dir ``openmax`` 与 filename
        在同一段，path 应能补回 ``openmax`` 不丢失。
        """
        above = [_line(
            (100, 100, 1500, 130),
            "media > gpu > openmax C+ openmax_video_decode_accelerator.cc",
        )]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "openmax_video_decode_accelerator.cc"
        assert m.path == (
            "media/gpu/openmax/openmax_video_decode_accelerator.cc"
        )

    def test_icon_word_C_not_treated_as_dir(self) -> None:
        """``C openmax.h`` 中 ``C`` 是 VSCode 图标，不能被视为 dir 段。"""
        above = [_line(
            (100, 100, 1200, 130),
            "media > gpu > openmax > C openmax_video_decode_accelerator.h",
        )]
        layout = _make_layout_with_above(above, anchor_count=1)
        m = extract_ide_metas(layout)[0]
        assert m.filename == "openmax_video_decode_accelerator.h"
        # path 不应混入额外的 ``C`` 段
        assert m.path == (
            "media/gpu/openmax/openmax_video_decode_accelerator.h"
        )

    def test_extension_to_language_coverage(self) -> None:
        """常见后缀 → 语言映射"""
        for ext, lang in [
            ("cc", "cpp"), ("h", "cpp"), ("py", "python"),
            ("gn", "gn"), ("rs", "rust"), ("ts", "typescript"),
        ]:
            above = [_line(
                (100, 100, 1000, 130),
                f"a > b > foo.{ext}",
            )]
            layout = _make_layout_with_above(above, anchor_count=1)
            m = extract_ide_metas(layout)[0]
            assert m.language == lang, f"ext={ext} expected {lang}"


class TestWithinImageReconcile:
    """同图栏间路径补全（场景 1：借用，场景 2：粘连还原）"""

    def test_path_inferred_from_peer(self) -> None:
        """col_0 只有 filename → 借用 col_1 的目录前缀"""
        above = [
            # col_0：tab 有 filename 无 breadcrumb（path=None）
            _line((180, 60, 800, 90), "C+ openmax_status.h 4×"),
            # col_1：完整 breadcrumb
            _line(
                (1990, 100, 2800, 130),
                "media >gpu >openmax > openmax_status.h",
            ),
        ]
        layout = _make_layout_with_above(above, anchor_count=2)
        metas = extract_ide_metas(layout)
        assert len(metas) == 2
        # col_0 path 应该被补成 col_1 的目录前缀
        assert metas[0].filename == "openmax_status.h"
        assert metas[0].path == "media/gpu/openmax/openmax_status.h"
        assert "code.path_inferred_from_peer" in metas[0].flags

    def test_path_segments_recovered(self) -> None:
        """col_0 的 path 段粘连（gpuopenmax）→ 用 col_1 的细分版本替换"""
        above = [
            # col_0：OCR 漏 `>` 导致 gpu+openmax 粘连
            _line(
                (180, 100, 1500, 130),
                "media >gpuopenmax > foo.cc",
            ),
            # col_1：正常细分
            _line(
                (1990, 100, 2800, 130),
                "media >gpu >openmax > bar.cc",
            ),
        ]
        layout = _make_layout_with_above(above, anchor_count=2)
        metas = extract_ide_metas(layout)
        assert metas[0].path == "media/gpu/openmax/foo.cc"
        assert "code.path_segments_recovered" in metas[0].flags

    def test_no_reconcile_when_no_peer_path(self) -> None:
        """所有栏都 path=None → 无可借用，保持 None"""
        above = [
            _line((180, 60, 800, 90), "C+ foo.cc 4×"),
            _line((1990, 60, 2800, 90), "C+ bar.cc 4×"),
        ]
        layout = _make_layout_with_above(above, anchor_count=2)
        metas = extract_ide_metas(layout)
        assert metas[0].path is None
        assert metas[1].path is None


class TestMultipleColumns:
    def test_two_columns_independent(self) -> None:
        """双栏：每栏独立 breadcrumb，各自归类"""
        above = [
            # column 0：x_center 在 200 附近（anchor[0].x1_min=200）
            _line(
                (180, 100, 1500, 130),
                "media >gpu >openmax > foo.cc",
            ),
            # column 1：x_center 在 2000 附近（anchor[1].x1_min=2000）
            _line(
                (1990, 100, 2800, 130),
                "media >gpu >openmax > BUILD.gn",
            ),
        ]
        layout = _make_layout_with_above(above, anchor_count=2)
        metas = extract_ide_metas(layout)
        assert len(metas) == 2
        assert metas[0].filename == "foo.cc"
        assert metas[0].language == "cpp"
        assert metas[1].filename == "BUILD.gn"
        assert metas[1].language == "gn"

    def test_no_anchors_returns_empty(self) -> None:
        empty = IDELayout(
            anchors=[], columns=[], above_code=[], below_code=[],
            sidebar=[], other=[], flags=["code.no_anchor"],
        )
        assert extract_ide_metas(empty) == []


# ---------- spike fixture 集成测试 ----------

SPIKE_LINES_DIR = (
    Path(__file__).resolve().parents[2] / "output" / "age8-probe-basic"
)
SPIKE_IMAGE_DIR = (
    Path(__file__).resolve().parents[2] / "test_images" / "age8-spike"
)


def _list_spike_stems() -> list[str]:
    if not SPIKE_LINES_DIR.exists():
        return []
    return sorted(
        d.name for d in SPIKE_LINES_DIR.iterdir()
        if (d / "lines.jsonl").exists()
    )


def _load_spike(stem: str) -> tuple[list[TextLine], tuple[int, int]]:
    p = SPIKE_LINES_DIR / stem / "lines.jsonl"
    items = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    text_lines = [
        TextLine(
            bbox=tuple(int(v) for v in it["bbox"][:4]),  # type: ignore[arg-type]
            text=it["text"],
            score=float(it.get("score", 1.0)),
        )
        for it in items
    ]
    from PIL import Image
    img = SPIKE_IMAGE_DIR / f"{stem}.JPG"
    if img.exists():
        return text_lines, Image.open(img).size
    return text_lines, (3400, 1900)


@pytest.mark.skipif(
    not _list_spike_stems(),
    reason="age8-probe-basic 数据未生成",
)
class TestSpike:
    @pytest.fixture(params=_list_spike_stems())
    def spike(self, request: pytest.FixtureRequest) -> tuple[
        str, list[TextLine], tuple[int, int],
    ]:
        return request.param, *_load_spike(request.param)

    def test_each_column_has_meta(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        """每栏都有一份 IDEMeta（即使 filename 为空）"""
        stem, lines, sz = spike
        layout = analyze_layout(lines, sz)
        metas = extract_ide_metas(layout)
        assert len(metas) == len(layout.anchors), f"{stem}"
        for meta in metas:
            assert isinstance(meta, IDEMeta)

    def test_at_least_one_filename_per_image(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        """8 张 spike 应至少有一栏拿到 filename"""
        stem, lines, sz = spike
        layout = analyze_layout(lines, sz)
        metas = extract_ide_metas(layout)
        any_filename = any(m.filename for m in metas)
        assert any_filename, f"{stem} 无任何栏识别出 filename"

    def test_known_filenames_detected(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        """spike 已知文件名应被识别"""
        stem, lines, sz = spike
        layout = analyze_layout(lines, sz)
        metas = extract_ide_metas(layout)
        all_filenames = " ".join(
            (m.filename or "") for m in metas
        ).lower()
        # spike 主要是 chromium openmax 相关
        if stem in {"DSC06835", "DSC06836", "DSC06837", "DSC06840"}:
            # 这些图都含 BUILD.gn 或 openmax_video_decode_accelerator
            has_known = (
                "openmax" in all_filenames
                or "build.gn" in all_filenames
                or "gles" in all_filenames
            )
            assert has_known, f"{stem} 文件名 {all_filenames!r} 与已知不符"
