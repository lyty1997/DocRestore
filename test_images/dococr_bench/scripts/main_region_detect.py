#!/usr/bin/env python
"""方案 A：基于文本框二维密度聚类（DBSCAN）的"主文档区域"检测。

输入：第一次 OCR 给出的文本框（像素坐标），以及图像尺寸。
输出：主文档区域的外接矩形（像素坐标），失败时返回 None。

设计原则（用户 2026-04-27 反馈）：
  绝对禁止使用任何"固定像素 / 固定归一化坐标 / 固定宽度"阈值。
  所有可调参数必须是从图像尺寸或当前数据统计派生出来的相对量，
  这样字体、焦段、屏幕分辨率、用户拖拽侧栏宽度变化时才不会漂移。

算法：
  1. 框中心 (cx, cy)；对每个点求第 k 近邻距离 d_k(p)
     —— k = MIN_SAMPLES（建议 3–4，文档场景对 k 不敏感）
  2. eps = percentile(d_k, EPS_PERCENTILE)
     —— 数据派生：保证 EPS_PERCENTILE% 的点能找到 k 个邻居形成核心；
        无任何绝对像素阈值，字体/焦段/屏幕变化全部自适应
  3. DBSCAN(eps, min_samples=k) 聚类
  4. 每个簇按"总文本框面积"打分，最大的簇 = 主文档区域
     —— 用面积而非数量，避免侧栏的密集小目录把主文档挤掉
  5. 簇内所有框的轴对齐外接矩形作裁剪框
     padding = 当前行高中位数 * PAD_LINE_HEIGHT_RATIO
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors

# 相对参数（全部数据派生，不依赖任何绝对像素值）
MIN_SAMPLES = 4                  # DBSCAN 核心点邻居数
EPS_PERCENTILE = 80              # 取 k 距离的 80% 分位作 eps
PAD_LINE_HEIGHT_RATIO = 0.6      # 裁剪框外边距 = 行高的 0.6 倍
MIN_CLUSTER_AREA_RATIO = 0.05    # 主簇面积至少占图像 5% 才算有效


@dataclass
class MainRegionResult:
    """主文档区域检测结果。"""

    bbox: tuple[int, int, int, int] | None  # (x1, y1, x2, y2) 像素，None 表失败
    n_total_boxes: int
    n_clusters: int
    main_cluster_size: int                 # 主簇文本框数
    main_cluster_area_ratio: float         # 主簇外接框面积 / 图像面积
    eps: float
    min_samples: int
    line_height: float


def detect_main_region(
    boxes: list[tuple[float, float, float, float]],
    image_size: tuple[int, int],
) -> MainRegionResult:
    """检测主文档区域。

    Args:
        boxes: 像素坐标的 (x1, y1, x2, y2) 列表
        image_size: (width, height)

    Returns:
        MainRegionResult，bbox 为 None 时表示无法判定主区域
    """
    width, height = image_size
    n = len(boxes)

    if n == 0 or width == 0 or height == 0:
        return MainRegionResult(
            bbox=None, n_total_boxes=n, n_clusters=0,
            main_cluster_size=0, main_cluster_area_ratio=0.0,
            eps=0.0, min_samples=0, line_height=0.0,
        )

    arr = np.asarray(boxes, dtype=np.float64)  # (N, 4)
    # 框中心
    centers = np.column_stack([
        (arr[:, 0] + arr[:, 2]) * 0.5,
        (arr[:, 1] + arr[:, 3]) * 0.5,
    ])
    # 行高（用框高度中位数；面对噪声更稳定）
    box_heights = arr[:, 3] - arr[:, 1]
    line_h = float(np.median(box_heights))
    if line_h <= 0:
        line_h = float(min(width, height)) * 0.01

    min_samples = MIN_SAMPLES
    # 数据点不足时退化为最小核心
    if n < min_samples + 1:
        return MainRegionResult(
            bbox=None, n_total_boxes=n, n_clusters=0,
            main_cluster_size=0, main_cluster_area_ratio=0.0,
            eps=0.0, min_samples=min_samples, line_height=line_h,
        )

    # k-distance 自适应 eps（k = min_samples，不含自身）
    nn = NearestNeighbors(n_neighbors=min_samples + 1)
    nn.fit(centers)
    dists, _ = nn.kneighbors(centers)
    kth = dists[:, min_samples]  # 第 k 近邻（去掉自身距离 0）
    eps = float(np.percentile(kth, EPS_PERCENTILE))
    if eps <= 0:
        eps = float(np.max(kth))

    labels = DBSCAN(eps=eps, min_samples=min_samples).fit(centers).labels_

    # 主簇判据（数据派生，无固定阈值）：
    #   score(c) = sum_box_area(c) * width_diversity(c)
    # width_diversity = 簇内文本框宽度标准差 / 平均宽度
    #   - 主文档：标题/正文行/短行混合 → 宽度多样 → diversity 高
    #   - 侧栏目录：每行宽度相近的窄列 → diversity 低
    # 这是"内部统计派生"的相对量，不引入几何先验
    box_widths = arr[:, 2] - arr[:, 0]
    box_areas = box_widths * (arr[:, 3] - arr[:, 1])

    cluster_ids = sorted(set(labels) - {-1})
    if not cluster_ids:
        return MainRegionResult(
            bbox=None, n_total_boxes=n, n_clusters=0,
            main_cluster_size=0, main_cluster_area_ratio=0.0,
            eps=eps, min_samples=min_samples, line_height=line_h,
        )

    def _cluster_score(cid: int) -> float:
        m = labels == cid
        size = int(m.sum())
        if size == 0:
            return 0.0
        widths = box_widths[m]
        mean_w = float(widths.mean())
        if mean_w <= 0:
            return 0.0
        diversity = float(widths.std()) / mean_w  # 变异系数 CV
        return float(box_areas[m].sum()) * (1.0 + diversity)

    best_cid = max(cluster_ids, key=_cluster_score)
    mask = labels == best_cid
    sel = arr[mask]
    x1 = float(sel[:, 0].min())
    y1 = float(sel[:, 1].min())
    x2 = float(sel[:, 2].max())
    y2 = float(sel[:, 3].max())

    pad = line_h * PAD_LINE_HEIGHT_RATIO
    x1 = max(0.0, x1 - pad)
    y1 = max(0.0, y1 - pad)
    x2 = min(float(width), x2 + pad)
    y2 = min(float(height), y2 + pad)

    cluster_area = (x2 - x1) * (y2 - y1)
    area_ratio = cluster_area / float(width * height)
    if area_ratio < MIN_CLUSTER_AREA_RATIO:
        # 主簇太小，可能整张图都没什么内容；不裁剪更安全
        return MainRegionResult(
            bbox=None, n_total_boxes=n, n_clusters=len(cluster_ids),
            main_cluster_size=int(mask.sum()),
            main_cluster_area_ratio=area_ratio,
            eps=eps, min_samples=min_samples, line_height=line_h,
        )

    return MainRegionResult(
        bbox=(int(x1), int(y1), int(x2), int(y2)),
        n_total_boxes=n,
        n_clusters=len(cluster_ids),
        main_cluster_size=int(mask.sum()),
        main_cluster_area_ratio=area_ratio,
        eps=eps,
        min_samples=min_samples,
        line_height=line_h,
    )
