# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IDE-UI 剪裁（AGE-8 Phase 1.1）

输入：一张 VSCode 暗色主题 IDE 截图
输出：剥掉 sidebar（文件树）/ top bar（tab+breadcrumb）/ bottom terminal 后的
"纯代码区"子图 + 各区域坐标元数据。

策略：
  - ``geometric``：找"低方差窄带"作为 IDE 面板之间的分隔条。分隔条的视觉
    特征是暗灰纯色 + 宽度 ≤ ~20px，对应的列/行标准差极低。
  - ``hybrid``（默认）：几何检测失败时按 fallback 比例（顶 8% / 底 20% /
    左 18%）裁剪，并在 ``flags`` 里打 ``*_fallback`` 标记。
  - ``ocr_anchored``：预留给后续 PR（AGE-8 P1.1 scope 内未实现）。

本模块只做"视觉切分"，不做 OCR / 文件归类（详见 AGE-8 P1.2 / P2）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DetectStrategy = Literal["geometric", "ocr_anchored", "hybrid"]
UsedStrategy = Literal["geometric", "fallback", "hybrid"]
BandSide = Literal["first_end", "first_start"]


@dataclass
class IDEUIConfig:
    """IDE-UI 剪裁配置

    Phase 1.1 用 dataclass 自足，AGE-51（P0 配置入口）会改为 pydantic
    BaseModel 嵌入 ``PipelineConfig.code.ide_ui``。
    """

    enable: bool = False
    detect_strategy: DetectStrategy = "hybrid"

    # fallback 比例（来源：8 张 spike 实测的 VSCode 典型布局）
    fallback_top_ratio: float = 0.08
    fallback_bottom_ratio: float = 0.20
    fallback_sidebar_ratio: float = 0.18

    # 几何检测阈值
    low_variance_threshold: float = 8.0  # 标准差 ≤ 此值视为纯色分隔条
    min_separator_width: int = 2         # 分隔条像素宽度下限（过滤偶发 1px 零方差列）
    max_separator_width: int = 20        # 分隔条像素宽度上限（超此视为区域）
    # 分隔条邻区 sanity check：真分隔条必然紧邻"高方差内容区"，否则视为稀疏区
    # 内部的伪分隔条（常见于合成图或 sidebar 纯色间隙），被过滤。
    neighbor_inspect_width: int = 50
    neighbor_content_threshold: float = 15.0

    # 搜索范围（原图对应维度的比例 [lo, hi]）
    sidebar_search_range: tuple[float, float] = (0.05, 0.30)
    top_search_range: tuple[float, float] = (0.02, 0.15)
    bottom_search_range: tuple[float, float] = (0.60, 0.95)


@dataclass
class IDEStripResult:
    """IDE-UI 剪裁结果"""

    cropped: Image.Image
    #: ``(left, top, right, bottom)``，在原图坐标系
    code_region_bbox: tuple[int, int, int, int]
    top_region_bbox: tuple[int, int, int, int]
    bottom_region_bbox: tuple[int, int, int, int] | None
    sidebar_region_bbox: tuple[int, int, int, int] | None
    detect_strategy: UsedStrategy
    flags: list[str] = field(default_factory=list)


def strip_ide_ui(
    image: Image.Image,
    config: IDEUIConfig | None = None,
) -> IDEStripResult:
    """剥掉 IDE 的 sidebar/tab/breadcrumb/terminal，返回纯代码区子图。

    不会 mutate 输入 image；返回的 ``cropped`` 是一个新的 PIL.Image。
    """
    cfg = config or IDEUIConfig()
    width, height = image.size
    if width < 100 or height < 100:
        raise ValueError(f"image too small for IDE-UI strip: {width}x{height}")

    # 灰度 + 转 int16（避免 uint8 求方差溢出）
    gray = np.asarray(image.convert("L"), dtype=np.int16)   # shape: (H, W)
    use_geometric = cfg.detect_strategy in ("geometric", "hybrid")

    # Step 1：全宽的行方差找 top / bottom 分隔线
    row_std = gray.std(axis=1)
    top_y = (
        _find_low_variance_band(
            row_std, height, cfg.top_search_range, cfg, take="first_end",
        )
        if use_geometric else None
    )
    bottom_y = (
        _find_low_variance_band(
            row_std, height, cfg.bottom_search_range, cfg, take="first_start",
        )
        if use_geometric else None
    )

    flags: list[str] = []
    any_fallback = False
    if top_y is None:
        top_y = int(height * cfg.fallback_top_ratio)
        flags.append("ide_strip.top_fallback")
        any_fallback = True
    if bottom_y is None:
        bottom_y = height - int(height * cfg.fallback_bottom_ratio)
        flags.append("ide_strip.bottom_fallback")
        any_fallback = True

    # Step 2：仅在 editor 行区间（top 下 / bottom 上）内算列方差找 sidebar。
    # 避免被 top bar / bottom terminal 区里的非分隔条像素污染列统计。
    editor_rows_lo = min(top_y, height - 1)
    editor_rows_hi = max(bottom_y, editor_rows_lo + 1)
    editor_slice = gray[editor_rows_lo:editor_rows_hi]
    sidebar_x = (
        _find_low_variance_band(
            editor_slice.std(axis=0),
            width,
            cfg.sidebar_search_range,
            cfg,
            take="first_end",
        )
        if use_geometric and editor_slice.shape[0] > 0
        else None
    )
    if sidebar_x is None:
        sidebar_x = int(width * cfg.fallback_sidebar_ratio)
        flags.append("ide_strip.sidebar_fallback")
        any_fallback = True

    # 保护：确保 top < bottom，sidebar < width
    sidebar_x = max(0, min(sidebar_x, width - 50))
    top_y = max(0, min(top_y, height - 50))
    bottom_y = max(top_y + 50, min(bottom_y, height))

    used: UsedStrategy
    if cfg.detect_strategy == "geometric":
        used = "fallback" if any_fallback else "geometric"
    elif any_fallback:
        used = "hybrid"
    else:
        used = "geometric"

    code_bbox = (sidebar_x, top_y, width, bottom_y)
    cropped = image.crop(code_bbox)

    logger.debug(
        "strip_ide_ui: strategy=%s sidebar_x=%d top_y=%d bottom_y=%d flags=%s",
        used, sidebar_x, top_y, bottom_y, flags,
    )

    return IDEStripResult(
        cropped=cropped,
        code_region_bbox=code_bbox,
        top_region_bbox=(0, 0, width, top_y),
        bottom_region_bbox=(0, bottom_y, width, height) if bottom_y < height else None,
        sidebar_region_bbox=(0, top_y, sidebar_x, bottom_y) if sidebar_x > 0 else None,
        detect_strategy=used,
        flags=flags,
    )


def _find_low_variance_band(
    profile: np.ndarray,
    total_len: int,
    search_range: tuple[float, float],
    cfg: IDEUIConfig,
    *,
    take: BandSide,
) -> int | None:
    """在 [search_range[0]*total, search_range[1]*total] 内扫描 profile，
    找连续的低方差段作为"分隔条"。

    ``take='first_end'``：返回第一条分隔条的"结束"坐标（用于 sidebar / top
    的"UI 结束位置"——VSCode 只有一条 sidebar↔editor 分隔线，找到即止）。
    ``take='first_start'``：返回第一条的"起始"坐标（用于 bottom terminal 的
    "UI 开始位置"）。
    宽度 < ``min_separator_width`` 的段视为噪声忽略。
    无符合条件的段 → 返回 None。
    """
    lo = max(int(total_len * search_range[0]), 0)
    hi = min(int(total_len * search_range[1]), total_len)
    if hi - lo < cfg.max_separator_width + 2:
        return None

    low_var = profile[lo:hi] <= cfg.low_variance_threshold
    if not bool(low_var.any()):
        return None

    # 扫连续 True 段，取第一个宽度合格且两侧有内容区的
    n = hi - lo
    i = 0
    while i < n:
        if low_var[i]:
            j = i
            while j < n and low_var[j]:
                j += 1
            run_width = j - i
            if cfg.min_separator_width <= run_width <= cfg.max_separator_width:
                gstart, gend = lo + i, lo + j
                if _has_content_neighbor(profile, gstart, gend, total_len, cfg):
                    if take == "first_end":
                        return gend
                    return gstart
            i = j
        else:
            i += 1

    return None


def _has_content_neighbor(
    profile: np.ndarray,
    band_start: int,
    band_end: int,
    total_len: int,
    cfg: IDEUIConfig,
) -> bool:
    """检查分隔条候选左/右邻区至少有一侧是高方差内容区。

    真分隔条紧邻 sidebar/editor/terminal 等内容区（高方差）；稀疏区内部偶发
    的零方差列两侧都是低方差，过滤掉。
    """
    w = cfg.neighbor_inspect_width
    left_lo = max(0, band_start - w)
    right_hi = min(total_len, band_end + w)

    left_slice = profile[left_lo:band_start]
    right_slice = profile[band_end:right_hi]

    min_content = cfg.neighbor_content_threshold

    def _median_above(slice_arr: np.ndarray) -> bool:
        return slice_arr.size > 0 and float(np.median(slice_arr)) > min_content

    return _median_above(left_slice) or _median_above(right_slice)
