# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""栏代码组装（AGE-8 Phase 1.3 v2）

把 ``IDELayout.columns`` 里的 TextLine 按"行号为骨架 + 代码 y 配对"
拼接成"该栏的源代码文本"，**保留缩进**。

**核心算法**：
  1. 行号/代码分离：每栏内 ``x1 ∈ [anchor.x1_min, anchor.x2_max+pad] +
     text=^\\d+$`` 是行号，其余是代码
  2. 字符像素宽度估算：median over (x2-x1)/len(text) 的代码 line
  3. 行高估算：median over 行号 line 的 (y2-y1)
  4. 行号 ↔ 代码按 y_center 配对（容差 = avg_line_height * 0.5）
  5. 缩进 = round((code.x1 - left_margin) / char_width)
  6. 缺号检测：anchor.num_range 与 OCR 实际行号集合 diff
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docrestore.models import TextLine
    from docrestore.processing.ide_layout import IDELayout, LineNumberAnchor

logger = logging.getLogger(__name__)

NUMERIC_RE = re.compile(r"^\d{1,4}$")


@dataclass
class AssemblyConfig:
    """组装配置（默认值在 8 张 spike 充分 work）"""

    #: 行号 line 的 x1 容差：≤ ``anchor.x2_max + tolerance`` 视为行号位置
    line_no_x_tolerance: int = 10
    #: y 配对容差比：相邻行的 y_center 距离 ≤ avg_line_height * ratio 视为同行
    y_match_tolerance_ratio: float = 0.55
    #: 字符宽度估算：仅取 text 长度 ≥ 此值的代码 line（避免短 line 噪声）
    char_width_min_text_len: int = 5
    #: 输出空行时的占位（None 即真空行；string 即写该字符串）
    empty_line_placeholder: str | None = None


@dataclass
class CodeLine:
    """组装后的一行代码"""

    line_no: int               # 行号（OCR 抽取或数值序列推断）
    text: str                  # 不含前导空格的代码内容（缩进单独存）
    indent: int                # 缩进字符数（按 char_width 推算）
    bbox: tuple[int, int, int, int] | None = None
    is_inferred_line_no: bool = False  # 行号是否推断而非 OCR 直读


@dataclass
class CodeColumn:
    """单栏代码组装结果"""

    column_index: int
    bbox: tuple[int, int, int, int]    # 该栏在原图坐标系的范围
    code_text: str                     # 完整代码文本（含缩进 + 换行）
    lines: list[CodeLine]
    char_width: float                  # 估算字符像素宽度
    avg_line_height: int               # 平均行高（像素）
    line_gaps: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def assemble_columns(
    layout: IDELayout,
    config: AssemblyConfig | None = None,
) -> list[CodeColumn]:
    """对 IDELayout 里每个栏做代码组装。

    Args:
        layout: ``analyze_layout`` 输出的布局结果。
        config: 可选组装参数。

    Returns:
        ``list[CodeColumn]``，与 ``layout.columns`` 一一对应；若 layout 无
        anchor，返回空列表（上游应据 quality flag 处理）。
    """
    cfg = config or AssemblyConfig()
    if not layout.anchors:
        return []

    columns: list[CodeColumn] = []
    pairs = zip(layout.anchors, layout.columns, strict=True)
    for idx, (anchor, lines) in enumerate(pairs):
        columns.append(_assemble_one_column(idx, anchor, lines, cfg))
    return columns


def _assemble_one_column(
    idx: int,
    anchor: LineNumberAnchor,
    column_lines: list[TextLine],
    cfg: AssemblyConfig,
) -> CodeColumn:
    """组装单个栏"""
    flags: list[str] = []

    # 1. 行号 / 代码 line 分离
    line_no_lines, code_lines = _split_line_numbers_and_code(
        column_lines, anchor, cfg.line_no_x_tolerance,
    )

    # 2. 字符宽度 + 行高估算
    char_width = _estimate_char_width(code_lines, cfg.char_width_min_text_len)
    avg_line_height = _estimate_line_height(line_no_lines)
    if char_width <= 0:
        flags.append("code.assembly.no_char_width")
        char_width = max(1.0, avg_line_height * 0.55)  # fallback：约半行高
    if avg_line_height <= 0:
        flags.append("code.assembly.no_line_height")
        avg_line_height = 30  # 兜底

    # 3. 代码区左边界（real left margin = 最小代码 x1）
    code_x1s = [ln.bbox[0] for ln in code_lines]
    left_margin = min(code_x1s) if code_x1s else anchor.x2_max + 1

    # 4. 行号 ↔ 代码 配对
    y_tolerance = max(1, int(avg_line_height * cfg.y_match_tolerance_ratio))
    paired, unpaired_codes = _pair_by_y(
        line_no_lines, code_lines, y_tolerance,
    )

    # 5. 构造 CodeLine 列表（按行号排序）
    assembled: list[CodeLine] = []
    for ln_no_line in sorted(line_no_lines, key=lambda ln: int(ln.text.strip())):
        line_no = int(ln_no_line.text.strip())
        codes = paired.get(id(ln_no_line), [])
        if not codes:
            assembled.append(CodeLine(
                line_no=line_no,
                text=cfg.empty_line_placeholder or "",
                indent=0,
                bbox=None,
                is_inferred_line_no=False,
            ))
            continue
        # 多 code line 同 y → 按 x1 排序拼接（OCR 把一行切多段）
        codes.sort(key=lambda ln: ln.bbox[0])
        first = codes[0]
        indent = max(
            0, round((first.bbox[0] - left_margin) / char_width),
        )
        joined_text = " ".join(c.text for c in codes)
        # 合并 bbox
        merged_bbox = (
            min(c.bbox[0] for c in codes),
            min(c.bbox[1] for c in codes),
            max(c.bbox[2] for c in codes),
            max(c.bbox[3] for c in codes),
        )
        assembled.append(CodeLine(
            line_no=line_no,
            text=joined_text,
            indent=indent,
            bbox=merged_bbox,
            is_inferred_line_no=False,
        ))

    # 6. 处理 unpaired 代码 line（行号缺失 → 用 y 推断行号插入）
    if unpaired_codes and assembled:
        assembled = _splice_unpaired_codes(
            assembled, unpaired_codes, left_margin, char_width, flags,
        )

    # 7. 缺号检测（OCR 行号集 vs anchor.num_range 期望集）
    line_gaps = _detect_line_number_gaps(line_no_lines, anchor)
    if line_gaps:
        flags.append(f"code.line_gap_count={len(line_gaps)}")

    # 8. 拼接最终代码文本（缩进用 N 个空格）
    code_text = _format_code_text(assembled)

    # 9. 栏 bbox（取代码 + 行号的并集）
    all_bboxes = [ln.bbox for ln in column_lines]
    if all_bboxes:
        bbox = (
            min(b[0] for b in all_bboxes),
            min(b[1] for b in all_bboxes),
            max(b[2] for b in all_bboxes),
            max(b[3] for b in all_bboxes),
        )
    else:
        bbox = (anchor.x1_min, anchor.y_top, anchor.x2_max, anchor.y_bottom)

    return CodeColumn(
        column_index=idx,
        bbox=bbox,
        code_text=code_text,
        lines=assembled,
        char_width=round(char_width, 2),
        avg_line_height=int(avg_line_height),
        line_gaps=line_gaps,
        flags=flags,
    )


def _split_line_numbers_and_code(
    column_lines: list[TextLine],
    anchor: LineNumberAnchor,
    x_tolerance: int,
) -> tuple[list[TextLine], list[TextLine]]:
    """把栏内 line 拆成行号 + 代码两组"""
    line_no_lines: list[TextLine] = []
    code_lines: list[TextLine] = []
    line_no_max_x = anchor.x2_max + x_tolerance
    for ln in column_lines:
        x1, _, x2, _ = ln.bbox
        text = ln.text.strip()
        is_numeric = bool(NUMERIC_RE.match(text))
        # 行号判定：纯数字 + x 在行号列范围
        if is_numeric and x1 >= anchor.x1_min - x_tolerance and x2 <= line_no_max_x:
            line_no_lines.append(ln)
        else:
            # 代码 line：在 anchor 之后；x1 < anchor.x1_min 的行（极少见）
            # 应在 ide_layout 阶段已被归入 sidebar，这里防御性丢弃
            if x1 >= anchor.x2_max - x_tolerance // 2:
                code_lines.append(ln)
    return line_no_lines, code_lines


def _estimate_char_width(
    code_lines: list[TextLine], min_text_len: int,
) -> float:
    """字符宽度 = (x2 - x1) / len(text) 的中位数（仅长 text）"""
    widths: list[float] = []
    for ln in code_lines:
        text = ln.text
        if len(text) < min_text_len:
            continue
        # 简单按 len 算；中文字符宽 ≈ 2 ASCII，为简化先不区分
        # spike 数据全是 ASCII 代码，可接受
        widths.append((ln.bbox[2] - ln.bbox[0]) / len(text))
    if not widths:
        return 0.0
    return statistics.median(widths)


def _estimate_line_height(line_no_lines: list[TextLine]) -> int:
    if not line_no_lines:
        return 0
    heights = [ln.bbox[3] - ln.bbox[1] for ln in line_no_lines]
    return int(statistics.median(heights))


def _pair_by_y(
    line_no_lines: list[TextLine],
    code_lines: list[TextLine],
    tolerance: int,
) -> tuple[dict[int, list[TextLine]], list[TextLine]]:
    """按 y_center 距离 ≤ tolerance 配对。

    返回 (paired_dict, unpaired_codes)：
      paired_dict: id(line_no_line) → [matched code lines]
      unpaired_codes: 未匹配上任何行号的代码 line
    """
    if not line_no_lines:
        return {}, list(code_lines)

    # 行号 line 按 y_center 索引；指定 key 避免 y_center 同值时 fallback
    # 比较 TextLine（dataclass 默认无 __lt__，会抛 TypeError）
    ln_by_yc: list[tuple[int, TextLine]] = sorted(
        (((ln.bbox[1] + ln.bbox[3]) // 2, ln) for ln in line_no_lines),
        key=lambda x: x[0],
    )

    paired: dict[int, list[TextLine]] = {id(ln): [] for ln in line_no_lines}
    unpaired: list[TextLine] = []

    for code in code_lines:
        code_yc = (code.bbox[1] + code.bbox[3]) // 2
        # 找 y_center 最近的行号 line
        best_ln: TextLine | None = None
        best_dist = tolerance + 1
        for ln_yc, ln in ln_by_yc:
            d = abs(ln_yc - code_yc)
            if d < best_dist:
                best_dist = d
                best_ln = ln
            if ln_yc - code_yc > tolerance:
                break  # 按 y 排序，可早停
        if best_ln is not None:
            paired[id(best_ln)].append(code)
        else:
            unpaired.append(code)

    return paired, unpaired


def _splice_unpaired_codes(
    assembled: list[CodeLine],
    unpaired_codes: list[TextLine],
    left_margin: int,  # noqa: ARG001 — 占位，升级为 y 推断插入时启用
    char_width: float,  # noqa: ARG001 — 同上
    flags: list[str],
) -> list[CodeLine]:
    """把未配对的代码（行号 OCR 漏识但代码识别到）按 y 推断行号插入。

    简化策略：当前版本只标 flag，不真插入——避免误插打乱编号。
    spike 实测中 unpaired 很少；若 273 全集 fail 率高，再升级为
    "用 y 位置在 assembled 相邻 line_no 之间推断行号"插入逻辑。
    """
    if not assembled:
        return assembled
    flags.append(f"code.assembly.unpaired_codes={len(unpaired_codes)}")
    return assembled


def _detect_line_number_gaps(
    line_no_lines: list[TextLine],
    anchor: LineNumberAnchor,
) -> list[int]:
    """检测 OCR 行号集与 anchor.num_range 期望连续序列的差集"""
    if not line_no_lines:
        return []
    actual = {int(ln.text.strip()) for ln in line_no_lines}
    lo, hi = anchor.num_range
    expected = set(range(lo, hi + 1))
    return sorted(expected - actual)


def _format_code_text(assembled: list[CodeLine]) -> str:
    """拼成最终代码文本（缩进用 N 个空格）"""
    parts: list[str] = []
    for line in assembled:
        prefix = " " * line.indent
        parts.append(prefix + line.text)
    return "\n".join(parts)
