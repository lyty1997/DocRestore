# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IDE 截图布局识别（AGE-8 Phase 1.2 v2）

基于"行号列锚点"的完全数据驱动布局分析。

**核心算法**：
  1. 从所有 OCR 行里筛出"行号 line"——text 严格匹配 ``^\\d{1,4}$`` + score ≥ 0.8
  2. 按 x1 精细聚类（容差 = 行号 bbox 自身宽度，相对而非绝对像素）
  3. 每簇若 ≥ N 行 + 数值近单调递增 → 一个"行号列锚点"，对应一个编辑器栏
  4. 栏数 = 锚点数；栏边界 = 相邻锚点 x 之间的区域
  5. 其余 OCR 行按 (x, y) 归类：tab 区 / sidebar / terminal / 噪声

**为什么用行号列**（IDE 编辑器的内在不变量）：
  - 字体大小无关：行号 bbox 容差是相对宽度
  - 栏宽拖拽无关：栏边界完全由锚点 x 推导
  - sidebar 折叠/展开无关：sidebar = 最左锚点之左
  - 任意栏数自适应（1/2/3/N 栏）：锚点数即栏数
  - 暗色/亮色主题无关：OCR 对纯数字识别极稳

8 张 spike 实测 100% 单调命中。详见
``docs/zh/backend/age-8-ide-code.md``。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from docrestore.models import TextLine

logger = logging.getLogger(__name__)

NUMERIC_RE = re.compile(r"^\d{1,4}$")


@dataclass
class LineNumberAnchor:
    """编辑器栏的"行号列"锚点"""

    x1_center: int            # 行号 bbox.x1 的均值
    x1_min: int               # 行号 bbox.x1 的最小值（栏左边界用）
    x2_max: int               # 行号 bbox.x2 的最大值（代码区起点用）
    y_top: int                # 行号 y_top 的最小值
    y_bottom: int             # 行号 y_bottom 的最大值
    line_count: int
    num_range: tuple[int, int]   # 行号数值起止
    monotonic_ratio: float       # 升序对占比（≥ 0.6 才算合格锚点）


@dataclass
class IDELayout:
    """IDE 截图布局识别结果"""

    anchors: list[LineNumberAnchor]
    #: 每个编辑器栏的代码行（已按 y 排序，仅含 anchor 范围内的 line）
    columns: list[list[TextLine]]
    above_code: list[TextLine]   # tab bar / breadcrumb / menu
    below_code: list[TextLine]   # terminal / status bar
    sidebar: list[TextLine]      # 文件树（折叠 = activity bar 图标，展开 = EXPLORER）
    other: list[TextLine] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class LayoutConfig:
    """行号列锚点检测配置"""

    #: 行号 bbox.score 阈值。低于此忽略（防 OCR 噪音）
    min_score: float = 0.8
    #: 行号 x1 聚类容差（像素）。VSCode 行号列 x 对齐极紧，20px 足够
    x1_cluster_bandwidth: int = 20
    #: 一簇最少多少行号才算锚点
    min_anchor_lines: int = 5
    #: 数值升序对占比下限
    min_monotonic_ratio: float = 0.6
    #: anchor.num_range 跨度上限。超此视为噪声 anchor。
    #:
    #: 设为 3000 的原因（基于 1259 张多数据集实测）：
    #: - 真长文件场景：TMedia DSC09871 行号 110-804（跨度 694），git diff 视图
    #:   行号跨度可达 1700-2000（diff 把整个文件 sparse 行号都展示）
    #: - 真噪声场景：chromium_video 含 PID/堆栈数字混入达 3700-5500，应过滤
    #: 3000 是平衡点：保留所有真 IDE/diff 视图，过滤极端噪声。
    max_num_range: int = 3000


def analyze_layout(
    text_lines: list[TextLine],
    image_size: tuple[int, int],
    config: LayoutConfig | None = None,
) -> IDELayout:
    """从 OCR 行级输出推导 IDE 布局。

    Args:
        text_lines: ``PageOCR.text_lines``（PaddleOCR basic pipeline 输出）。
        image_size: 原图 (width, height)，用于 sidebar 范围判定。
        config: 可选的检测配置。

    Returns:
        IDELayout：anchor + 区域归类结果 + quality flags。
    """
    cfg = config or LayoutConfig()
    flags: list[str] = []

    if not text_lines:
        flags.append("code.no_text_lines")
        return IDELayout([], [], [], [], [], flags=flags)

    anchors = _find_line_number_columns(text_lines, cfg)
    if not anchors:
        flags.append("code.no_anchor")
        return IDELayout([], [], [], [], list(text_lines), flags=flags)

    if len(anchors) == 1:
        flags.append("code.single_anchor")
    elif len(anchors) >= 3:
        flags.append("code.three_plus_anchors")

    weak_mono = [a for a in anchors if a.monotonic_ratio < 0.8]
    if weak_mono:
        flags.append("code.weak_monotonic")

    image_width, _ = image_size
    columns, above, below, sidebar, other = _assign_regions(
        text_lines, anchors, image_width,
    )

    # 每栏内按 y 排序
    for col in columns:
        col.sort(key=lambda ln: ln.bbox[1])
    above.sort(key=lambda ln: ln.bbox[1])
    below.sort(key=lambda ln: ln.bbox[1])
    sidebar.sort(key=lambda ln: ln.bbox[1])

    return IDELayout(
        anchors=anchors,
        columns=columns,
        above_code=above,
        below_code=below,
        sidebar=sidebar,
        other=other,
        flags=flags,
    )


def _find_line_number_columns(
    text_lines: list[TextLine],
    cfg: LayoutConfig,
) -> list[LineNumberAnchor]:
    """筛行号 line → 按 x1 聚类 → 检查单调性 → 返回合格锚点。"""
    numeric = [
        ln for ln in text_lines
        if NUMERIC_RE.match(ln.text.strip()) and ln.score >= cfg.min_score
    ]
    if not numeric:
        return []

    numeric.sort(key=lambda ln: ln.bbox[0])

    # 按 x1 聚类
    clusters: list[list[TextLine]] = [[numeric[0]]]
    for ln in numeric[1:]:
        prev = clusters[-1][-1]
        if ln.bbox[0] - prev.bbox[0] > cfg.x1_cluster_bandwidth:
            clusters.append([ln])
        else:
            clusters[-1].append(ln)

    anchors: list[LineNumberAnchor] = []
    for cluster in clusters:
        if len(cluster) < cfg.min_anchor_lines:
            continue
        cluster_sorted = sorted(cluster, key=lambda ln: ln.bbox[1])
        nums = [int(ln.text.strip()) for ln in cluster_sorted]
        if len(nums) < 2:
            continue
        ascending = sum(1 for i in range(len(nums) - 1) if nums[i + 1] > nums[i])
        ratio = ascending / (len(nums) - 1)
        if ratio < cfg.min_monotonic_ratio:
            continue
        # 跨度过大 → 噪声（如 EXPLORER 文件名误识）
        if (max(nums) - min(nums)) > cfg.max_num_range:
            continue
        x1s = [ln.bbox[0] for ln in cluster]
        x2s = [ln.bbox[2] for ln in cluster]
        ys_top = [ln.bbox[1] for ln in cluster]
        ys_bot = [ln.bbox[3] for ln in cluster]
        anchors.append(LineNumberAnchor(
            x1_center=int(sum(x1s) / len(x1s)),
            x1_min=min(x1s),
            x2_max=max(x2s),
            y_top=min(ys_top),
            y_bottom=max(ys_bot),
            line_count=len(cluster),
            num_range=(min(nums), max(nums)),
            monotonic_ratio=round(ratio, 3),
        ))

    anchors.sort(key=lambda a: a.x1_center)
    return anchors


def _assign_regions(
    text_lines: list[TextLine],
    anchors: list[LineNumberAnchor],
    image_width: int,
) -> tuple[
    list[list[TextLine]],   # columns
    list[TextLine],         # above_code
    list[TextLine],         # below_code
    list[TextLine],         # sidebar
    list[TextLine],         # other
]:
    """把每行 line 归类到 column_i / tab / terminal / sidebar。

    判定优先级（决策树）：
      1. y < 所有锚点最小 y_top → above_code（tab/menu）
      2. x < 第一锚点 x1_min → sidebar（无论 y 多大，含 sidebar 文件树底部
         越界进 below_code 区的情况，AGE-8 P1.2 v2 issue 的修复点）
      3. y > 所有锚点最大 y_bottom → below_code（terminal/status bar）
      4. 落入某锚点的 [x1_min, 下一个锚点 x1_min) → 该栏
      5. 其他 → other
    """
    code_y_top = min(a.y_top for a in anchors)
    code_y_bot = max(a.y_bottom for a in anchors)
    leftmost_anchor_x = anchors[0].x1_min

    # 每栏的 x 范围：[anchor.x1_min, 下一锚点 x1_min) 或到图右
    column_spans: list[tuple[int, int]] = []
    for i, anchor in enumerate(anchors):
        right = anchors[i + 1].x1_min - 1 if i + 1 < len(anchors) else image_width
        column_spans.append((anchor.x1_min, right))

    columns: list[list[TextLine]] = [[] for _ in anchors]
    above: list[TextLine] = []
    below: list[TextLine] = []
    sidebar: list[TextLine] = []
    other: list[TextLine] = []

    for ln in text_lines:
        x1, y1, x2, y2 = ln.bbox
        # 用 bbox 中心点判定区域归属，避免 breadcrumb / status bar 等
        # "y 与 anchor 边界重叠" 的 line 误归 column 后污染代码
        y_center = (y1 + y2) // 2
        x_center = (x1 + x2) // 2

        if y_center < code_y_top:
            above.append(ln)
            continue
        if x_center < leftmost_anchor_x:
            sidebar.append(ln)
            continue
        if y_center > code_y_bot:
            below.append(ln)
            continue

        assigned = False
        for i, (col_left, col_right) in enumerate(column_spans):
            if col_left <= x1 <= col_right:
                columns[i].append(ln)
                assigned = True
                break
        if not assigned:
            other.append(ln)

    return columns, above, below, sidebar, other
