# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IDE 布局识别单测（AGE-8 Phase 1.2 v2）

策略：
  - 合成 fixture：手工构造 list[TextLine] 模拟 1/2/3 栏 IDE
  - spike fixture：从 output/age8-probe-basic/<stem>/lines.jsonl 读真数据
    （CI 无该数据时 skip，遵循 CLAUDE.md 测试规则）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docrestore.models import TextLine
from docrestore.processing.ide_layout import (
    IDELayout,
    LayoutConfig,
    LineNumberAnchor,
    analyze_layout,
)


# ---------- 合成 fixture ----------

def _line(
    bbox: tuple[int, int, int, int], text: str, score: float = 0.95,
) -> TextLine:
    return TextLine(bbox=bbox, text=text, score=score)


def _make_single_column_fixture() -> list[TextLine]:
    """单栏：1 个行号列 + 代码"""
    lines: list[TextLine] = []
    # 行号列在 x1=200，10 行
    for i in range(1, 11):
        lines.append(_line((200, 100 + i * 30, 230, 130 + i * 30), str(i)))
    # 代码在 x1=300
    for i in range(1, 11):
        lines.append(_line(
            (300, 100 + i * 30, 800, 130 + i * 30),
            f"code_line_{i}",
        ))
    # tab bar 在顶部
    lines.append(_line((100, 20, 600, 60), "tab.cc"))
    # terminal 在底部
    lines.append(_line((100, 600, 800, 640), "PROBLEMS TERMINAL"))
    return lines


def _make_double_column_fixture() -> list[TextLine]:
    """双栏：左 anchor x1=200，右 anchor x1=1500"""
    lines: list[TextLine] = []
    for i in range(1, 11):
        # 左栏行号
        lines.append(_line((200, 100 + i * 30, 230, 130 + i * 30), str(i)))
        # 左栏代码
        lines.append(_line((300, 100 + i * 30, 1200, 130 + i * 30), f"left_{i}"))
        # 右栏行号（数值 18-27 模拟滚动后的范围）
        lines.append(_line(
            (1500, 100 + i * 30, 1530, 130 + i * 30), str(i + 17),
        ))
        # 右栏代码
        lines.append(_line(
            (1600, 100 + i * 30, 2400, 130 + i * 30), f"right_{i}",
        ))
    # tab bar
    lines.append(_line((100, 20, 600, 60), "left.cc"))
    lines.append(_line((1400, 20, 2000, 60), "right.gn"))
    return lines


def _make_no_anchor_fixture() -> list[TextLine]:
    """无行号列（VSCode hide line numbers 的情况）"""
    return [
        _line((100, 100, 800, 130), "no line numbers here"),
        _line((100, 140, 800, 170), "just code without anchor"),
    ]


# ---------- 单元测试 ----------

class TestSingleColumn:
    def test_one_anchor_detected(self) -> None:
        layout = analyze_layout(
            _make_single_column_fixture(), image_size=(2000, 800),
        )
        assert isinstance(layout, IDELayout)
        assert len(layout.anchors) == 1
        anchor = layout.anchors[0]
        assert isinstance(anchor, LineNumberAnchor)
        assert anchor.line_count == 10
        assert anchor.num_range == (1, 10)
        assert anchor.monotonic_ratio == 1.0
        assert "code.single_anchor" in layout.flags

    def test_one_column_with_tab_and_terminal_classified(self) -> None:
        layout = analyze_layout(
            _make_single_column_fixture(), image_size=(2000, 800),
        )
        assert len(layout.columns) == 1
        # 代码 10 行 + 行号 10 行 都进 column_0（在 anchor 范围）
        assert len(layout.columns[0]) >= 10
        # tab 在顶部
        assert any("tab.cc" in ln.text for ln in layout.above_code)
        # terminal 在底部
        assert any("PROBLEMS" in ln.text for ln in layout.below_code)


class TestDoubleColumn:
    def test_two_anchors_detected(self) -> None:
        layout = analyze_layout(
            _make_double_column_fixture(), image_size=(2500, 800),
        )
        assert len(layout.anchors) == 2
        # 第一个 anchor x 较小
        assert layout.anchors[0].x1_center < layout.anchors[1].x1_center
        # 数值范围
        assert layout.anchors[0].num_range == (1, 10)
        assert layout.anchors[1].num_range == (18, 27)

    def test_columns_dont_cross_mix(self) -> None:
        layout = analyze_layout(
            _make_double_column_fixture(), image_size=(2500, 800),
        )
        # 左栏代码不出现在右栏，反之亦然
        col0_text = " ".join(ln.text for ln in layout.columns[0])
        col1_text = " ".join(ln.text for ln in layout.columns[1])
        assert "left_" in col0_text
        assert "right_" not in col0_text
        assert "right_" in col1_text
        assert "left_" not in col1_text


class TestNoAnchor:
    def test_no_anchor_flag(self) -> None:
        layout = analyze_layout(
            _make_no_anchor_fixture(), image_size=(2000, 800),
        )
        assert layout.anchors == []
        assert "code.no_anchor" in layout.flags

    def test_empty_input_flag(self) -> None:
        layout = analyze_layout([], image_size=(2000, 800))
        assert layout.anchors == []
        assert "code.no_text_lines" in layout.flags


class TestThresholds:
    def test_low_score_filtered(self) -> None:
        """score < min_score 的行号被忽略"""
        lines = [_line((200, 100 + i * 30, 230, 130 + i * 30), str(i), score=0.5)
                 for i in range(1, 11)]
        layout = analyze_layout(
            lines, image_size=(2000, 800),
            config=LayoutConfig(min_score=0.8),
        )
        assert layout.anchors == []

    def test_non_monotonic_filtered(self) -> None:
        """乱序数字（非单调）不算行号列"""
        nums = ["3", "7", "1", "9", "2", "8", "4", "6"]
        lines = [
            _line((200, 100 + i * 30, 230, 130 + i * 30), n)
            for i, n in enumerate(nums)
        ]
        layout = analyze_layout(lines, image_size=(2000, 800))
        # 升序对占比 < 0.6 → 不通过
        assert layout.anchors == []

    def test_excessive_num_range_filtered(self) -> None:
        """num_range 跨度 > max_num_range 视为噪声 anchor 过滤

        构造：8 个单调数字但跨度 = 5500（如 chromium_video 堆栈 PID 噪声）
        """
        nums = ["1", "100", "500", "1000", "2000", "3000", "4500", "5500"]
        lines = [
            _line((200, 100 + i * 30, 230, 130 + i * 30), n)
            for i, n in enumerate(nums)
        ]
        layout = analyze_layout(lines, image_size=(2000, 800))
        # 默认 max_num_range=3000 → 跨度 5499 应被过滤
        assert layout.anchors == []
        assert "code.no_anchor" in layout.flags

    def test_long_file_passes(self) -> None:
        """真长文件（行号跨度 < 3000）应通过"""
        # 行号跨度 = 800（如 IDE 滚动到 file 中段：行号 200-1000）
        nums = ["200", "300", "400", "500", "600", "700", "850", "1000"]
        lines = [
            _line((200, 100 + i * 30, 230, 130 + i * 30), n)
            for i, n in enumerate(nums)
        ]
        layout = analyze_layout(lines, image_size=(2000, 800))
        assert len(layout.anchors) == 1
        assert layout.anchors[0].num_range == (200, 1000)

    def test_custom_max_num_range_strict(self) -> None:
        """收紧 max_num_range 可过滤掉本来通过的 anchor"""
        nums = ["1", "100", "200", "400", "600", "800"]
        lines = [
            _line((200, 100 + i * 30, 230, 130 + i * 30), n)
            for i, n in enumerate(nums)
        ]
        layout = analyze_layout(
            lines, image_size=(2000, 800),
            config=LayoutConfig(max_num_range=500),
        )
        assert layout.anchors == []


# ---------- spike 真实数据 fixture ----------

SPIKE_LINES_DIR = Path(__file__).resolve().parents[2] / "output" / "age8-probe-basic"


def _list_spike_stems() -> list[str]:
    if not SPIKE_LINES_DIR.exists():
        return []
    return sorted(
        d.name for d in SPIKE_LINES_DIR.iterdir()
        if (d / "lines.jsonl").exists()
    )


_SPIKE_IMAGE_DIR = (
    Path(__file__).resolve().parents[2] / "test_images" / "age8-spike"
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
    # 用 PIL 读原图尺寸
    from PIL import Image  # noqa: PLC0415 — 测试局部导入
    img_path = _SPIKE_IMAGE_DIR / f"{stem}.JPG"
    if img_path.exists():
        return text_lines, Image.open(img_path).size
    return text_lines, (3400, 1900)


@pytest.mark.skipif(
    not _list_spike_stems(),
    reason="age8-probe-basic 数据未生成（需先跑 scripts/age8_probe_basic_ocr.py）",
)
class TestSpikeImages:
    """8 张 spike 集成验证（已知 100% 单调命中）"""

    @pytest.fixture(params=_list_spike_stems())
    def spike(self, request: pytest.FixtureRequest) -> tuple[
        str, list[TextLine], tuple[int, int],
    ]:
        stem = request.param
        text_lines, size = _load_spike(stem)
        return stem, text_lines, size

    def test_at_least_one_anchor(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        assert layout.anchors, f"{stem} 未检出行号列锚点"

    def test_high_monotonic(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        for a in layout.anchors:
            assert a.monotonic_ratio >= 0.9, (
                f"{stem} anchor x1={a.x1_center} mono={a.monotonic_ratio} 偏低"
            )

    def test_each_column_has_lines(
        self, spike: tuple[str, list[TextLine], tuple[int, int]],
    ) -> None:
        stem, text_lines, size = spike
        layout = analyze_layout(text_lines, size)
        for i, col in enumerate(layout.columns):
            assert col, f"{stem} column_{i} 为空（异常）"
