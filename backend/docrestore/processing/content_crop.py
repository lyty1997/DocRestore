# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""文档正文区裁剪：OCR 前检测正文主列、裁掉左右侧栏。

屏摄文档照片含左侧导航树 / 右侧大纲 / 顶部浏览器 UI；文档模式无行号锚定，
这些非正文元素会污染正文 OCR（用户此前靠人工裁剪规避）。本模块在 OCR 前用图像
版面分析检测正文主列的左右边界、裁出正文，替代人工裁剪。

MVP：只检测左右边界、纵向取整高。详见 ``docs/zh/doc-content-crop.md``。

契约：任何失败 / 检测不可靠都**回退原图**，绝不中断下游 OCR。
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
from cv2.typing import MatLike
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

#: OpenCV BGR 图像类型别名。
ImageBGR = MatLike

#: 垂直投影只取中间高度带 ``[top, bottom]``，避开顶部浏览器 UI 与底部设备 / 桌面。
_BAND_TOP = 0.18
_BAND_BOTTOM = 0.82
#: 抑制图像最外侧 ratio 宽度的二值化边缘伪影（伪影尖峰会污染列段）。
_EDGE_RATIO = 0.02
#: 文本列密度阈值（相对峰值）：正文列远高于此，与侧栏间深沟壑（~2–3%）低于此被分隔。
_LEVEL_RATIO = 0.08
#: 1D 闭运算核宽（相对图宽）：仅填正文内部极小空隙，须 < 侧栏沟壑宽以免连进侧栏。
_CLOSE_RATIO = 0.015
#: 横向膨胀核：把每行文字连成实条，强化"列"结构。
_DILATE_KERNEL = (61, 3)
#: 自适应二值化参数（blockSize 须为奇数 / C 为常数减项），经样本调校。
_ADAPTIVE_BLOCK = 31
_ADAPTIVE_C = 15


def _runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """返回布尔序列里 True 的连续段 ``[(start, end_exclusive), ...]``。"""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, int(mask.shape[0])))
    return out


def detect_content_lr(image_bgr: ImageBGR) -> tuple[int, int] | None:
    """检测正文主列左右边界 ``(x0, x1)``；检测不可靠返回 ``None``（不抛异常）。

    流程：自适应二值化取文字 → 横向膨胀连行 → 中间高度带垂直投影 → 去边缘伪影 →
    低阈值取文本列（正文列连续，与侧栏间的深沟壑被分隔）→ 取**包含图像水平中心**的
    连续列段（正文居中先验，规避左导航文字密集导致的峰值误锚）。
    """
    if image_bgr.size == 0:
        return None
    h, w = image_bgr.shape[:2]
    if w < 10 or h < 10:
        return None
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV, _ADAPTIVE_BLOCK, _ADAPTIVE_C,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, _DILATE_KERNEL)
    dil = cv2.dilate(binary, kernel, iterations=2)
    band = dil[int(_BAND_TOP * h):int(_BAND_BOTTOM * h), :]
    col = (band > 0).sum(axis=0).astype(np.float32)
    col = cv2.blur(col.reshape(1, -1), (1, 25)).ravel().astype(np.float32)
    peak = float(col.max())
    if peak <= 0:
        return None
    edge = int(_EDGE_RATIO * w)
    if edge > 0:
        col[:edge] = 0.0
        col[-edge:] = 0.0
    mask: NDArray[np.bool_] = col > peak * _LEVEL_RATIO
    close_w = max(11, int(_CLOSE_RATIO * w))
    ck = cv2.getStructuringElement(cv2.MORPH_RECT, (close_w, 1))
    closed_src = (mask.astype(np.uint8) * 255).reshape(1, -1)
    closed: NDArray[np.bool_] = (
        cv2.morphologyEx(closed_src, cv2.MORPH_CLOSE, ck).ravel() > 0
    )
    segs = _runs(closed)
    if not segs:
        return None
    center = w // 2
    cand = [s for s in segs if s[0] <= center < s[1]]
    x0, x1 = cand[0] if cand else max(segs, key=lambda s: s[1] - s[0])
    if x1 - x0 < 2:
        return None
    return int(x0), int(x1)


#: 框宽 / 图宽 > 此比例 → 无明显侧栏（已人工裁剪 / 全屏文档）→ 跳过裁剪（恒等放行）。
_SKIP_RATIO = 0.9
#: 框宽 / 图宽 < 此比例 → 正文不应这么窄，视为误检 → 跳过回退原图（宁可不裁）。
_MIN_RATIO = 0.2


def compute_crop_box(image_bgr: ImageBGR) -> tuple[int, int] | None:
    """计算正文裁剪框 ``(x0, x1)``；应跳过则返回 ``None``（恒等放行原图）。

    在 ``detect_content_lr`` 基础上加"已裁剪 / 误检跳过"判据：

    - 框宽占比 > ``_SKIP_RATIO``：图中无明显侧栏（已人工裁剪 / 全屏文档）→ 跳过。
    - 框宽占比 < ``_MIN_RATIO``：正文不应这么窄，视为误检 → 跳过回退原图。

    其余（合理占比）返回裁剪框；纵向由调用方取整高（MVP）。
    """
    box = detect_content_lr(image_bgr)
    if box is None:
        return None
    x0, x1 = box
    w = int(image_bgr.shape[1])
    if w <= 0:
        return None
    ratio = (x1 - x0) / w
    if ratio > _SKIP_RATIO or ratio < _MIN_RATIO:
        return None
    return box
