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

"""区域样式采样（PPT 版面定位导出 §11.1）：从源图区域采前景 / 背景色。

捕获期（OCR 引擎内）调用——唯一同时握有「源图像素 + 像素 bbox + image_size
一致三元组」的地方（导出期 ``doc_dir`` 只有输出 ``images/``，源图已不在）。
纯函数 + numpy 向量化 + 全套弃权守卫 + fail-safe：拿不准就返回 ``None``（渲染退
默认黑字无填充），绝不抛异常炸掉 OCR 主流程。算法见设计文档 §11.1。
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

#: RGB 三元组（0..255）
Rgb: TypeAlias = tuple[int, int, int]

# ── 弃权 / 采样阈值（模块级常量便于调）─────────────────────────
_MIN_ROI_SIDE = 6  # ROI 任一维下限（像素）
_MIN_ROI_PIXELS = 64  # 核心区面积下限（像素）
_INSET_RATIO = 0.06  # ROI 各边内缩比例（去 bbox 边缘混入的相邻底色 / 线框）
_SAMPLE_TARGET = 4000  # 降采样目标采样点（控成本，恒定开销）
_QUANT_SHIFT = 5  # 每通道右移位数 → 8 级量化（粗量化抗 JPEG / 抗锯齿色散，真机调优）
_QUANT_LEVELS = 8  # 量化级数（2 ** (8 - _QUANT_SHIFT)）
_QUANT_BINS = _QUANT_LEVELS**3  # 512 桶
_MIN_BG_FRAC = 0.15  # 背景主色占比下限（真机屏摄背景色散到邻桶，阈值按真机调低）
_MAX_FG_FRAC = 0.45  # 前景占样本比例上限（更高=双色块 / 图）
_MIN_CONTRAST = 60.0  # 前景背景 RGB 欧氏距离下限（0..441）
_MIN_LUMA_DELTA = 28.0  # 前景背景亮度差下限（0..255）

#: BT.601 亮度权重
_LUMA_W = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _luma(rgb: NDArray[np.float32]) -> float:
    """RGB → BT.601 亮度（0..255）。"""
    return float(np.dot(rgb, _LUMA_W))


def _to_rgb(arr: NDArray[np.float32]) -> Rgb:
    """numpy 三元素均值色 → clamp 进 [0,255] 的整型 ``Rgb``。"""
    r = int(round(float(arr[0])))
    g = int(round(float(arr[1])))
    b = int(round(float(arr[2])))
    return (
        max(0, min(255, r)),
        max(0, min(255, g)),
        max(0, min(255, b)),
    )


def _extract_samples(
    img_rgb: NDArray[np.uint8], bbox: tuple[int, int, int, int],
) -> NDArray[np.float32] | None:
    """bbox → 内缩 + 降采样后的样本像素 ``(N, 3) float32``；过小 / 非法返回 ``None``。

    bbox 越界自动 clamp；各边内缩 ``_INSET_RATIO`` 去边缘相邻底色；按整数步长
    降采样到 ≤ ``_SAMPLE_TARGET`` 点（不插值，不引抗锯齿过渡色）。
    """
    h_img, w_img = img_rgb.shape[:2]
    x1 = max(0, min(int(bbox[0]), w_img))
    y1 = max(0, min(int(bbox[1]), h_img))
    x2 = max(0, min(int(bbox[2]), w_img))
    y2 = max(0, min(int(bbox[3]), h_img))
    if x2 - x1 < _MIN_ROI_SIDE or y2 - y1 < _MIN_ROI_SIDE:
        return None
    roi = img_rgb[y1:y2, x1:x2, :3]
    rh, rw = roi.shape[:2]
    dh, dw = int(rh * _INSET_RATIO), int(rw * _INSET_RATIO)
    if rh - 2 * dh >= _MIN_ROI_SIDE and rw - 2 * dw >= _MIN_ROI_SIDE:
        roi = roi[dh : rh - dh, dw : rw - dw]
    ch, cw = roi.shape[:2]
    if ch * cw < _MIN_ROI_PIXELS:
        return None
    step = max(1, int(np.sqrt(ch * cw / _SAMPLE_TARGET)))
    samp = roi[::step, ::step].reshape(-1, 3).astype(np.float32)
    if samp.shape[0] < _MIN_ROI_PIXELS:  # 降采样后过少 → 退用全核心区
        samp = roi.reshape(-1, 3).astype(np.float32)
    if samp.shape[0] < _MIN_ROI_PIXELS:
        return None
    return samp


def _quantize(samp: NDArray[np.float32]) -> NDArray[np.intp]:
    """每通道 16 级量化 → 每点桶索引（0.._QUANT_BINS-1）。"""
    q = samp.astype(np.int32) >> _QUANT_SHIFT
    idx = q[:, 0] * (_QUANT_LEVELS * _QUANT_LEVELS) + q[:, 1] * _QUANT_LEVELS + q[:, 2]
    result: NDArray[np.intp] = idx.astype(np.intp)
    return result


def _pick_foreground_bin(
    counts: NDArray[np.intp], bg_bin: int, bg_rgb: NDArray[np.float32],
) -> int | None:
    """前景桶 = ``count × 到背景色距`` 最大的非背景桶；无第二色返回 ``None``。"""
    nonzero = np.nonzero(counts)[0]
    if nonzero.size < 2:  # 只有一种量化色 → 无前景 / 背景之分
        return None
    hi_r = (nonzero // (_QUANT_LEVELS * _QUANT_LEVELS)) % _QUANT_LEVELS
    hi_g = (nonzero // _QUANT_LEVELS) % _QUANT_LEVELS
    hi_b = nonzero % _QUANT_LEVELS
    half = 1 << (_QUANT_SHIFT - 1)
    centers = (
        np.stack([hi_r, hi_g, hi_b], axis=1) * (1 << _QUANT_SHIFT) + half
    ).astype(np.float32)
    dist = np.linalg.norm(centers - bg_rgb[None, :], axis=1)
    score = counts[nonzero].astype(np.float32) * dist
    score[nonzero == bg_bin] = -1.0  # 排除背景桶自身
    return int(nonzero[int(score.argmax())])


def _classify(samp: NDArray[np.float32]) -> tuple[Rgb, Rgb] | None:
    """样本像素 → (前景色, 背景色)；任一守卫不过返回 ``None``（弃权退默认）。

    背景=最大量化桶（占多数，面积判别对暗色模式成立）；前景=最强对比次色。
    守卫：背景需占主导、前景不能过半（双色块）、前景背景对比 + 亮度差需足够。
    """
    n = samp.shape[0]
    idx = _quantize(samp)
    counts = np.bincount(idx, minlength=_QUANT_BINS).astype(np.intp)
    bg_bin = int(counts.argmax())
    if int(counts[bg_bin]) / n < _MIN_BG_FRAC:  # 背景不占主导 → 像图 / 渐变
        return None
    bg_rgb = samp[idx == bg_bin].mean(axis=0).astype(np.float32)
    fg_bin = _pick_foreground_bin(counts, bg_bin, bg_rgb)
    if fg_bin is None:
        return None
    fg_mask = idx == fg_bin
    if int(fg_mask.sum()) / n > _MAX_FG_FRAC:  # 次色过半 → 双色块 / 图标
        return None
    fg_rgb = samp[fg_mask].mean(axis=0).astype(np.float32)
    if float(np.linalg.norm(fg_rgb - bg_rgb)) < _MIN_CONTRAST:  # 对比不足
        return None
    if abs(_luma(fg_rgb) - _luma(bg_rgb)) < _MIN_LUMA_DELTA:  # 亮度差不足
        return None
    return (_to_rgb(fg_rgb), _to_rgb(bg_rgb))


def sample_region_color(
    img_rgb: NDArray[np.uint8], bbox: tuple[int, int, int, int],
) -> tuple[Rgb, Rgb] | None:
    """从源图区域采 ``(前景色, 背景色)``；拿不准返回 ``None``（fail-safe 退默认）。

    img_rgb：整页 RGB 数组 ``(H, W, 3) uint8``（调用方解码一次、全页区域共享）。
    bbox：``(x1, y1, x2, y2)`` 像素，落在 img_rgb 尺寸内（越界自动 clamp）。
    返回 ``((fg_r, fg_g, fg_b), (bg_r, bg_g, bg_b))`` 或 ``None``。算法见 §11.1。
    """
    try:
        samp = _extract_samples(img_rgb, bbox)
        if samp is None:
            return None
        return _classify(samp)
    except Exception:  # noqa: BLE001 — 采样 best-effort，任何异常退 None 不炸主流程
        return None
