#!/usr/bin/env python3
# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

# mypy: ignore-errors

"""AGE-8 "行号列锚点"布局分析验证

输入：age8-probe-basic/<stem>/lines.jsonl（每行：{bbox, text, score}）
目标：基于"行号列"识别编辑器栏数与栏边界，验证算法可行性。

算法：
  1. 从 lines 中筛出"行号行"：text 严格匹配 ^\\d+$，且数值 < 10000
  2. 按 x1 精细聚类（bandwidth=20px，行号列对齐极紧）
  3. 每个聚类若 ≥ 5 个行号 + 值序列近单调递增 → 一个"行号列锚点"
  4. 栏数 = 行号列数
  5. 每栏代码区 bbox：
       x ∈ (L_i.x_right, L_{i+1}.x_left) 或 (L_last.x_right, image_right)
       y ∈ (L_i.y_top, L_i.y_bottom)
  6. 其余 line 根据 (x,y) 判归 tab / terminal / sidebar / 噪声
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IN_ROOT = PROJECT_ROOT / "output" / "age8-probe-basic"
OUT_ROOT = PROJECT_ROOT / "output" / "age8-line-layout"

NUMERIC_RE = re.compile(r"^\d{1,4}$")


def _load_lines(stem: str) -> list[dict]:
    p = IN_ROOT / stem / "lines.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _find_line_number_columns(
    lines: list[dict],
    x_bandwidth: int = 20,
    min_run: int = 5,
) -> list[dict]:
    """找"行号列"锚点。

    步骤：
      a. 筛出 text 是 1-4 位纯数字 + score >= 0.8 的行
      b. 按 x1 精细聚类，gap > bandwidth 开新簇
      c. 对每簇：按 y 排序 → 检查数值序列是否"近单调递增"
         （允许少量 OCR 错识导致的乱序 + gap）
      d. 返回每个合格簇的锚点：x1_center, x2_max, y_top, y_bottom, 行号数量
    """
    numeric = [
        ln for ln in lines
        if NUMERIC_RE.match((ln.get("text") or "").strip())
        and ln.get("score", 0) >= 0.8
    ]
    if not numeric:
        return []

    # 按 x1 聚类
    numeric.sort(key=lambda ln: ln["bbox"][0])
    clusters: list[list[dict]] = [[numeric[0]]]
    for ln in numeric[1:]:
        if ln["bbox"][0] - clusters[-1][-1]["bbox"][0] > x_bandwidth:
            clusters.append([ln])
        else:
            clusters[-1].append(ln)

    anchors: list[dict] = []
    for cluster in clusters:
        if len(cluster) < min_run:
            continue
        # 按 y 排序数值序列检查"近单调"
        cluster_sorted = sorted(cluster, key=lambda ln: ln["bbox"][1])
        nums = [int(ln["text"].strip()) for ln in cluster_sorted]
        # 允许 OCR 错：统计"升序对"占比
        ascending_pairs = sum(
            1 for i in range(len(nums) - 1) if nums[i + 1] > nums[i]
        )
        monotonic_ratio = (
            ascending_pairs / (len(nums) - 1) if len(nums) > 1 else 0
        )
        if monotonic_ratio < 0.6:
            continue
        x1s = [ln["bbox"][0] for ln in cluster]
        x2s = [ln["bbox"][2] for ln in cluster]
        ys_top = [ln["bbox"][1] for ln in cluster]
        ys_bot = [ln["bbox"][3] for ln in cluster]
        anchors.append({
            "x1_center": int(sum(x1s) / len(x1s)),
            "x1_min": min(x1s),
            "x2_max": max(x2s),
            "y_top": min(ys_top),
            "y_bottom": max(ys_bot),
            "line_count": len(cluster),
            "num_range": [min(nums), max(nums)],
            "monotonic_ratio": round(monotonic_ratio, 3),
        })
    # 按 x1 从左到右
    anchors.sort(key=lambda a: a["x1_center"])
    return anchors


def _assign_regions(
    lines: list[dict],
    anchors: list[dict],
    image_width: int,
) -> dict[str, list[dict]]:
    """把每个行归类到 tab / sidebar / column_i / terminal / other"""
    if not anchors:
        return {"all_lines": lines}
    code_y_top = min(a["y_top"] for a in anchors)
    code_y_bot = max(a["y_bottom"] for a in anchors)

    regions: dict[str, list[dict]] = {
        "above_code": [],
        "below_code": [],
        "left_of_editors": [],
    }
    for i in range(len(anchors)):
        regions[f"column_{i}"] = []

    # 定义每栏的 x 范围：[anchor.x2_max, 下一个 anchor.x1_min) 或到图右
    column_spans = []
    for i, anchor in enumerate(anchors):
        left = anchor["x2_max"] + 1
        right = (
            anchors[i + 1]["x1_min"] - 1
            if i + 1 < len(anchors) else image_width
        )
        column_spans.append((anchor["x1_min"], left, right))

    for ln in lines:
        x1, y1, x2, y2 = ln["bbox"]
        if y2 < code_y_top:
            regions["above_code"].append(ln)
            continue
        if y1 > code_y_bot:
            regions["below_code"].append(ln)
            continue
        # 在代码 y 区间
        if x2 < anchors[0]["x1_min"]:
            regions["left_of_editors"].append(ln)
            continue
        # 判哪栏
        assigned = False
        for i, (anchor_x1min, code_left, code_right) in enumerate(column_spans):
            if anchor_x1min <= x1 <= code_right:
                regions[f"column_{i}"].append(ln)
                assigned = True
                break
        if not assigned:
            regions.setdefault("other", []).append(ln)
    return regions


def _render_report(
    stem: str,
    lines: list[dict],
    anchors: list[dict],
    regions: dict[str, list[dict]],
    image_size: tuple[int, int],
) -> str:
    out: list[str] = []
    w, h = image_size
    out.append(f"=== {stem} ===")
    out.append(f"image_size={w}x{h}")
    out.append(f"total_lines={len(lines)}")
    out.append("")
    out.append(f"line-number columns detected: {len(anchors)}")
    for i, a in enumerate(anchors):
        out.append(
            f"  anchor {i}: x1={a['x1_center']} ({a['x1_center']/w:.1%})  "
            f"y=[{a['y_top']},{a['y_bottom']}] ({a['y_top']/h:.1%}-{a['y_bottom']/h:.1%})  "
            f"lines={a['line_count']}  nums={a['num_range']}  "
            f"mono={a['monotonic_ratio']}"
        )
    out.append("")
    for key, region_lines in regions.items():
        out.append(f"region={key}  count={len(region_lines)}")
        for ln in sorted(region_lines, key=lambda x: x["bbox"][1])[:8]:
            x1, y1, x2, y2 = ln["bbox"]
            text = (ln.get("text") or "")[:90].replace("\n", "⏎")
            out.append(
                f"  x=[{x1:>5},{x2:>5}] y=[{y1:>4},{y2:>4}]  {text}"
            )
        out.append("")
    return "\n".join(out) + "\n"


def _get_image_size(stem: str) -> tuple[int, int]:
    # 从 res.json 读（paddleocr 保存的）
    p = IN_ROOT / stem / f"{stem}_res.json"
    if p.exists():
        data = json.loads(p.read_text())
        inner = data.get("res", data)
        shape = inner.get("doc_preprocessor_res", {}).get(
            "output_img_shape",
        ) or inner.get("input_img_shape")
        if isinstance(shape, (list, tuple)) and len(shape) >= 2:
            return (int(shape[1]), int(shape[0]))
    # fallback 用 PIL
    try:
        from PIL import Image
        img_path = PROJECT_ROOT / "test_images" / "age8-spike" / f"{stem}.JPG"
        if img_path.exists():
            return Image.open(img_path).size
    except Exception:  # noqa: BLE001
        pass
    return (0, 0)


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if not IN_ROOT.exists():
        print(f"no input: {IN_ROOT}")
        return 1
    stems = [d.name for d in IN_ROOT.iterdir() if d.is_dir()]
    for stem in stems:
        lines = _load_lines(stem)
        if not lines:
            print(f"skip {stem}: no lines")
            continue
        img_size = _get_image_size(stem)
        anchors = _find_line_number_columns(lines)
        regions = _assign_regions(lines, anchors, img_size[0])
        report = _render_report(stem, lines, anchors, regions, img_size)
        print(report)
        (OUT_ROOT / f"{stem}.report.txt").write_text(report, encoding="utf-8")
        (OUT_ROOT / f"{stem}.anchors.json").write_text(
            json.dumps(anchors, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"\n→ {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
