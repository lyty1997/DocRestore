# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""VSCode split editor 多栏代码区切割（AGE-8 Phase 1.2）

输入：``ide_ui_strip`` 输出的"纯代码区"子图
输出：``list[Column]``，每个 Column 是独立的代码区子图。同图不同栏 ≠ 同文件
（AGE-8 决策 #3，不跨栏合并）。

实现思路（与 ide_ui_strip 一致的"低方差带 = 分隔条"启发式）：
  - 对代码区算每列标准差
  - 找"位置居中（20%-80%）+ 宽度 [min, max]px + 低方差"的分隔带
  - 检测到一条 → 返回左右两 Column；检测不到 → 单 Column

>2 栏暂不支持（spike 里没见），触发 ``code.columns_exceed`` 信号。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class ColumnsConfig:
    """多栏切割配置

    Phase 1.2 用 dataclass 自足，AGE-51 会迁到 pydantic 嵌入
    ``PipelineConfig.code.columns``。
    """

    enable: bool = True

    # 中央分隔条的几何阈值
    low_variance_threshold: float = 8.0     # 与 ide_ui_strip 保持一致
    min_separator_width: int = 4            # VSCode split editor 间隙典型 8-12px
    max_separator_width: int = 20
    min_separator_height_ratio: float = 0.80  # 分隔带需贯穿 ≥ 80% 高度
    # 分隔条位置需落在 [lo, hi] 范围（原图宽度比例）
    search_range: tuple[float, float] = (0.20, 0.80)
    # 邻区 sanity check：分隔条两侧需是内容（代码）
    neighbor_inspect_width: int = 50
    neighbor_content_threshold: float = 15.0

    # fallback：图宽/高 ≥ 此比例时硬切 50/50
    fallback_aspect_ratio: float = 1.6


@dataclass
class Column:
    """一个代码栏"""

    image: Image.Image
    #: ``(left, top, right, bottom)`` 在输入子图（非原 IDE 截图）坐标系
    bbox: tuple[int, int, int, int]
    column_index: int


@dataclass
class ColumnsResult:
    """多栏切割结果"""

    columns: list[Column]
    detected_split: bool  # 是否几何检测到分隔条（否则单栏或 fallback 硬切）
    flags: list[str] = field(default_factory=list)


def split_columns(
    image: Image.Image,
    config: ColumnsConfig | None = None,
) -> ColumnsResult:
    """把"纯代码区"切成 N 列。

    输入：``ide_ui_strip`` 的 cropped 图像（已剥掉 IDE UI）。
    行为：
      - 单栏：未检到分隔条 + 宽高比 < fallback_aspect_ratio
      - 双栏：检到一条合法分隔条 → 左右切
      - fallback 硬切：未检到分隔条但宽高比 ≥ fallback_aspect_ratio → 50/50
    """
    cfg = config or ColumnsConfig()
    width, height = image.size
    if width < 100 or height < 100:
        raise ValueError(f"code region too small: {width}x{height}")

    flags: list[str] = []

    separator = _find_central_vertical_separator(image, cfg)

    if separator is not None:
        sep_start, sep_end = separator
        left = _make_column(image, bbox=(0, 0, sep_start, height), index=0)
        right = _make_column(
            image, bbox=(sep_end, 0, width, height), index=1,
        )
        return ColumnsResult(columns=[left, right], detected_split=True, flags=flags)

    if width / max(height, 1) >= cfg.fallback_aspect_ratio:
        # 宽高比很夸张 → 肉眼看像双屏，硬切 50/50
        mid = width // 2
        left = _make_column(image, bbox=(0, 0, mid, height), index=0)
        right = _make_column(image, bbox=(mid, 0, width, height), index=1)
        flags.append("code.column_fallback_split")
        logger.info("split_columns: fallback 50/50 at aspect=%.2f", width / height)
        return ColumnsResult(columns=[left, right], detected_split=False, flags=flags)

    # 单栏
    only = _make_column(image, bbox=(0, 0, width, height), index=0)
    return ColumnsResult(columns=[only], detected_split=False, flags=flags)


def _make_column(
    image: Image.Image, *, bbox: tuple[int, int, int, int], index: int,
) -> Column:
    return Column(image=image.crop(bbox), bbox=bbox, column_index=index)


def _find_central_vertical_separator(
    image: Image.Image,
    cfg: ColumnsConfig,
) -> tuple[int, int] | None:
    """在代码区中央找一条垂直低方差分隔带。

    返回 ``(start_x, end_x)`` 或 None。只取第一个满足条件的；spike 里没见过 > 2
    栏，如果真出现多条，调用方可通过 flags 看 ``code.columns_exceed`` 信号。
    """
    width, _ = image.size
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    col_std = gray.std(axis=0)

    lo = int(width * cfg.search_range[0])
    hi = int(width * cfg.search_range[1])
    lo = max(lo, 0)
    hi = min(hi, width)
    if hi - lo < cfg.max_separator_width + 2:
        return None

    low_var_mask = col_std[lo:hi] <= cfg.low_variance_threshold
    if not bool(low_var_mask.any()):
        return None

    n = hi - lo
    i = 0
    matches: list[tuple[int, int]] = []
    while i < n:
        if low_var_mask[i]:
            j = i
            while j < n and low_var_mask[j]:
                j += 1
            run_width = j - i
            if cfg.min_separator_width <= run_width <= cfg.max_separator_width:
                gstart, gend = lo + i, lo + j
                if _has_content_neighbor(col_std, gstart, gend, width, cfg):
                    matches.append((gstart, gend))
            i = j
        else:
            i += 1

    if not matches:
        return None
    # 多条 → 取最靠中央的（VSCode 双栏分隔通常在图中间）
    center = width / 2
    return min(matches, key=lambda m: abs(((m[0] + m[1]) / 2) - center))


def _has_content_neighbor(
    profile: np.ndarray,
    band_start: int,
    band_end: int,
    total_len: int,
    cfg: ColumnsConfig,
) -> bool:
    """分隔条左右至少一侧邻区是高方差（代码内容），避免检到"全空白"区。"""
    w = cfg.neighbor_inspect_width
    left_lo = max(0, band_start - w)
    right_hi = min(total_len, band_end + w)
    left_slice = profile[left_lo:band_start]
    right_slice = profile[band_end:right_hi]
    min_content = cfg.neighbor_content_threshold

    def _median_above(arr: np.ndarray) -> bool:
        return arr.size > 0 and float(np.median(arr)) > min_content

    return _median_above(left_slice) or _median_above(right_slice)
