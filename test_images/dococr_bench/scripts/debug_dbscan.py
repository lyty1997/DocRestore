#!/usr/bin/env python
"""调试 DBSCAN 聚类结果：在原图上把不同簇用不同颜色画出来。

用法：
  python debug_dbscan.py <res_json> <orig_image> <out_image>

读取 res.json 拿文本行 polys → 跑 DBSCAN（用与 main_region_detect 同样参数）
→ 在原图上画框，每个簇一种颜色，noise 用灰色。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cv2
import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors

import main_region_detect as mr
from run_sv3_dbscan import collect_text_boxes


def debug(res_json_path: Path, img_path: Path, out_path: Path) -> None:
    data = json.loads(res_json_path.read_text())
    bboxes, (W, H) = collect_text_boxes(data)
    img = cv2.imread(str(img_path))
    if img is None:
        raise SystemExit(f"读不到图: {img_path}")
    h, w = img.shape[:2]

    arr = np.asarray(bboxes, dtype=np.float64)
    centers = np.column_stack([
        (arr[:, 0] + arr[:, 2]) * 0.5,
        (arr[:, 1] + arr[:, 3]) * 0.5,
    ])
    box_h = arr[:, 3] - arr[:, 1]
    line_h = float(np.median(box_h))
    n = len(bboxes)
    min_samples = mr.MIN_SAMPLES
    nn = NearestNeighbors(n_neighbors=min_samples + 1).fit(centers)
    dists, _ = nn.kneighbors(centers)
    kth = dists[:, min_samples]
    eps = float(np.percentile(kth, mr.EPS_PERCENTILE))
    labels = DBSCAN(eps=eps, min_samples=min_samples).fit(centers).labels_

    print(
        f"[{img_path.name}] N={n} line_h={line_h:.1f} "
        f"k-dist p50={np.percentile(kth, 50):.0f} "
        f"p80={eps:.0f} max={kth.max():.0f} min_samples={min_samples}"
    )
    cluster_ids = sorted(set(labels) - {-1})
    print(f"  clusters: {len(cluster_ids)} noise: {(labels == -1).sum()}")

    box_areas = (arr[:, 2] - arr[:, 0]) * (arr[:, 3] - arr[:, 1])
    for cid in cluster_ids:
        mask = labels == cid
        sel = arr[mask]
        x1, y1 = sel[:, 0].min(), sel[:, 1].min()
        x2, y2 = sel[:, 2].max(), sel[:, 3].max()
        print(
            f"  cluster {cid}: n={mask.sum()} "
            f"bbox=({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}) "
            f"size=({x2 - x1:.0f}x{y2 - y1:.0f}) "
            f"sum_box_area={box_areas[mask].sum():.0f}"
        )

    # 颜色调色板
    palette = np.array([
        (255, 80, 80), (80, 255, 80), (80, 80, 255),
        (255, 255, 80), (255, 80, 255), (80, 255, 255),
        (200, 120, 0), (0, 200, 200), (200, 0, 200),
        (120, 200, 0), (200, 0, 120), (0, 120, 200),
    ], dtype=np.int32)

    vis = img.copy()
    # noise 灰色
    for i in range(n):
        x1, y1, x2, y2 = arr[i]
        if labels[i] == -1:
            cv2.rectangle(
                vis, (int(x1), int(y1)), (int(x2), int(y2)),
                (160, 160, 160), 2,
            )
    # 簇用对应颜色
    for cid in cluster_ids:
        color = tuple(int(x) for x in palette[cid % len(palette)])
        for i in np.where(labels == cid)[0]:
            x1, y1, x2, y2 = arr[i]
            cv2.rectangle(
                vis, (int(x1), int(y1)), (int(x2), int(y2)),
                color, 4,
            )

    # 画主区域候选框
    if cluster_ids:
        best_cid = max(
            cluster_ids,
            key=lambda cid: float(box_areas[labels == cid].sum()),
        )
        sel = arr[labels == best_cid]
        x1 = int(sel[:, 0].min())
        y1 = int(sel[:, 1].min())
        x2 = int(sel[:, 2].max())
        y2 = int(sel[:, 3].max())
        pad = int(line_h * mr.PAD_LINE_HEIGHT_RATIO)
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 255), 12)

    # 缩到 1280 写出
    scale = min(1280 / max(h, w), 1.0)
    if scale < 1.0:
        vis = cv2.resize(
            vis, (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), vis, [cv2.IMWRITE_JPEG_QUALITY, 85])


def main() -> None:
    if len(sys.argv) != 4:
        print("usage: debug_dbscan.py <res_json> <orig_image> <out_image>")
        sys.exit(1)
    debug(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))


if __name__ == "__main__":
    main()
