# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""代码模式 Stage 0：每页行账本完整性校验（AGE-79）

把单个 ``CodeColumn`` 的逐行明细加工成可信的 ``LineLedger``，让污染/错配行
**不进入** 跨页内容比对（S2）。核心原则：先保证「源干净」，行号 + 行内容
才能作为比文件名更硬的真相源。

三项确定性校验（零 LLM、低成本）：
  1. **行号单调性**：照片的垂直顺序是物理真相，OCR 的行号数值才是待校验对象。
     按视觉 y 升序，line_no 必须严格递增；违例（如 88 误识成 8）标违例并降为
     非锚点。重复行号同样标违例。
  2. **inferred 行号**：``is_inferred_line_no=True``（行号是 y 推断而非 OCR
     直读）天然不可作跨页锚点。
  3. **回查原图 OCR**：用 ``CodeLine.bbox`` 溯源 ``PageOCR.text_lines`` 对应
     区域，重建文本与 ``CodeLine.text`` 比相似度——验证没把相邻行文本错配到
     本行号；同时回填命中行的平均 OCR ``score`` 作为该行 ``confidence``
     （``CodeLine`` 本身不携带 OCR score，这是新增信号，供 S2/S3 共识加权）。

输出：``build_line_ledger(page_stem, column, text_lines) -> LineLedger``。
``anchor_trustable=True`` 的行才是 S2 重合区比对的合法锚点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from docrestore.models import TextLine
from docrestore.processing.code_assembly import CodeColumn, CodeLine

#: 回查原图 OCR 时，重建文本与 ``CodeLine.text`` 的相似度下限。低于则判配对存疑。
DEFAULT_FAITHFUL_MIN_RATIO = 0.80
#: bbox 包含判定的像素容差（OCR bbox 抖动兜底）。
DEFAULT_BBOX_TOLERANCE = 4


@dataclass(frozen=True)
class LedgerConfig:
    """Stage 0 校验阈值（后续可并入 ``CodeRestoreConfig``）。"""

    faithful_min_ratio: float = DEFAULT_FAITHFUL_MIN_RATIO
    bbox_tolerance: int = DEFAULT_BBOX_TOLERANCE


@dataclass
class LineEntry:
    """单行行账本项：行号 -> 可信的（文本 + 缩进 + 可作锚点 + 置信度）。"""

    line_no: int
    text: str
    indent: int
    anchor_trustable: bool
    confidence: float


@dataclass
class LineLedger:
    """单个 ``PageColumn`` 的行账本，``entries`` 以 line_no 为键。"""

    page_stem: str
    column_index: int
    entries: dict[int, LineEntry] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def trustable_anchors(self) -> dict[int, LineEntry]:
        """仅返回可作跨页比对锚点的行（``anchor_trustable=True``）。"""
        return {
            no: e for no, e in self.entries.items() if e.anchor_trustable
        }


def build_line_ledger(
    page_stem: str,
    column: CodeColumn,
    text_lines: list[TextLine],
    config: LedgerConfig | None = None,
) -> LineLedger:
    """对单个 ``CodeColumn`` 做三项校验，产出可信行账本。

    Args:
        page_stem: 来源图片 stem（与 column_index 一起作为 ledger 的键）。
        column: 已组装的代码栏。
        text_lines: 该页原始 OCR 行级输出（回查忠实性 + 回填置信度用）。
        config: 可选阈值，缺省用模块默认值。

    Returns:
        ``LineLedger``；``column.lines`` 为空时返回 entries 为空的 ledger（不抛）。
    """
    cfg = config or LedgerConfig()
    ledger = LineLedger(page_stem=page_stem, column_index=column.column_index)
    lines = column.lines
    if not lines:
        return ledger

    duplicates = _duplicate_line_nos(lines)
    y_outliers = _y_monotonic_outliers(lines)

    nonmonotonic = 0
    suspect = 0
    inferred = 0
    for line in lines:
        faithful, confidence = _verify_against_ocr(line, text_lines, cfg)
        trustable = True
        # 空行/占位行：无内容可作锚点。
        if line.bbox is None or not line.text.strip():
            trustable = False
        if line.is_inferred_line_no:
            trustable = False
            inferred += 1
        if line.line_no in duplicates or id(line) in y_outliers:
            trustable = False
            nonmonotonic += 1
        if not faithful:
            trustable = False
            suspect += 1
        _insert_entry(ledger.entries, LineEntry(
            line_no=line.line_no,
            text=line.text,
            indent=line.indent,
            anchor_trustable=trustable,
            confidence=round(confidence, 4),
        ))

    _append_count_flag(ledger.flags, "code.line.nonmonotonic", nonmonotonic)
    _append_count_flag(ledger.flags, "code.line.pairing_suspect", suspect)
    _append_count_flag(ledger.flags, "code.line.inferred", inferred)
    return ledger


def _insert_entry(entries: dict[int, LineEntry], entry: LineEntry) -> None:
    """写入 entry；同行号已存在时保留 confidence 更高者（重复行已被标不可信）。"""
    existing = entries.get(entry.line_no)
    if existing is None or entry.confidence > existing.confidence:
        entries[entry.line_no] = entry


def _duplicate_line_nos(lines: list[CodeLine]) -> set[int]:
    """同一栏内重复出现的行号集合（至少两次）。"""
    seen: set[int] = set()
    dups: set[int] = set()
    for line in lines:
        if line.line_no in seen:
            dups.add(line.line_no)
        seen.add(line.line_no)
    return dups


def _y_monotonic_outliers(lines: list[CodeLine]) -> set[int]:
    """按视觉 y（bbox top）升序，line_no 未严格递增的行 ``id()`` 集合。

    照片垂直顺序是物理真相；某行 line_no 在 y 升序里没有严格大于此前最大值，
    即行号误识（如 88 读成 8、或重复）→ 标违例。无 bbox 的行（空行占位）跳过。
    """
    ordered: list[tuple[int, int, int]] = []  # (y_top, line_no, id)
    for line in lines:
        bbox = line.bbox
        if bbox is None:
            continue
        ordered.append((bbox[1], line.line_no, id(line)))
    ordered.sort(key=lambda item: item[0])

    outliers: set[int] = set()
    running_max: int | None = None
    for _, line_no, ident in ordered:
        if running_max is not None and line_no <= running_max:
            outliers.add(ident)
        else:
            running_max = line_no
    return outliers


def _verify_against_ocr(
    line: CodeLine, text_lines: list[TextLine], cfg: LedgerConfig,
) -> tuple[bool, float]:
    """回查原图 OCR：重建 bbox 内文本并比相似度，同时回填 OCR 置信度。

    Returns:
        ``(是否忠实, 置信度)``。置信度 = 命中 text_lines 的平均 ``score`` ×
        重建相似度。无 bbox（空行）返回 ``(True, 0.0)``——不可回查但也不算存疑，
        其不可信由调用方按「空行不作锚点」单独处理。
    """
    bbox = line.bbox
    if bbox is None:
        return True, 0.0
    matched = [
        tl for tl in text_lines
        if _bbox_contained(tl.bbox, bbox, cfg.bbox_tolerance)
    ]
    if not matched:
        # bbox 非空却无对应原始行 → 无法确认忠实性，判存疑。
        return False, 0.0
    matched.sort(key=lambda tl: tl.bbox[0])
    reconstructed = " ".join(tl.text for tl in matched)
    ratio = _similarity(reconstructed, line.text)
    avg_score = sum(tl.score for tl in matched) / len(matched)
    return ratio >= cfg.faithful_min_ratio, avg_score * ratio


def _bbox_contained(
    inner: tuple[int, int, int, int],
    outer: tuple[int, int, int, int],
    tol: int,
) -> bool:
    """``inner`` 是否（在像素容差内）被 ``outer`` 包含。"""
    inner_x1, inner_y1, inner_x2, inner_y2 = inner
    outer_x1, outer_y1, outer_x2, outer_y2 = outer
    return (
        inner_x1 >= outer_x1 - tol
        and inner_y1 >= outer_y1 - tol
        and inner_x2 <= outer_x2 + tol
        and inner_y2 <= outer_y2 + tol
    )


def _similarity(left: str, right: str) -> float:
    """归一空白后的字符串相似度（0–1）。"""
    norm_left = " ".join(left.split())
    norm_right = " ".join(right.split())
    if not norm_left and not norm_right:
        return 1.0
    return SequenceMatcher(None, norm_left, norm_right).ratio()


def _append_count_flag(flags: list[str], code: str, count: int) -> None:
    """计数 > 0 时追加 ``code=count`` 形式的质量 flag。"""
    if count > 0:
        flags.append(f"{code}={count}")
