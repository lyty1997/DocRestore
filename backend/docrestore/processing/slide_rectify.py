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

"""幻灯片透视矫正（S2 / AGE-86）

屏摄 PPT 照片 → 检测幻灯片屏幕四边形 → warpPerspective 矫正为正视图。
- detect_slide_quad: Otsu 亮区 → 最大外轮廓 → approxPolyDP 取 4 角
- rectify: 透视变换 + 顶边上抬补暗标题栏
- rectify_page: 逐页异步入口，落盘 before/after 对照，失败回退原图

本模块是 PPT 模式 OCR 前的逐页前处理，纯图像处理、不依赖 PipelineConfig
（解耦）；接入由 S5 的 producer hook 从 PowerPointRestoreConfig 取字段后调用。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

#: OpenCV 图像类型别名（BGR 通道，uint8）
ImageBGR = NDArray[np.uint8]

#: approxPolyDP epsilon 相对周长比例：值越大越激进地把轮廓简化成少边形
_APPROX_EPSILON_RATIO = 0.02
#: 候选四边形面积至少占全图比例，过滤掉小亮斑（反光点 / logo）
_MIN_AREA_RATIO = 0.2


@dataclass(frozen=True)
class Quad:
    """幻灯片屏幕四角点（像素坐标），顺序固定为 左上 右上 右下 左下。"""

    top_left: tuple[int, int]
    top_right: tuple[int, int]
    bottom_right: tuple[int, int]
    bottom_left: tuple[int, int]

    def as_array(self) -> NDArray[np.float32]:
        """转成 OpenCV 透视变换用的 4x2 float32 数组（同样顺序）。"""
        return np.array(
            [
                self.top_left,
                self.top_right,
                self.bottom_right,
                self.bottom_left,
            ],
            dtype=np.float32,
        )


def _dist(a: NDArray[np.float32], b: NDArray[np.float32]) -> float:
    """两点欧氏距离。"""
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _order_corners(pts: NDArray[np.float32]) -> Quad:
    """把任意顺序的 4 个角点排成 左上 / 右上 / 右下 / 左下。

    经典方法：x+y 最小为左上、最大为右下；y-x 最小为右上、最大为左下。
    """
    s = pts.sum(axis=1)
    diff = pts[:, 1] - pts[:, 0]  # y - x
    tl = pts[int(np.argmin(s))]
    br = pts[int(np.argmax(s))]
    tr = pts[int(np.argmin(diff))]
    bl = pts[int(np.argmax(diff))]
    return Quad(
        top_left=(int(tl[0]), int(tl[1])),
        top_right=(int(tr[0]), int(tr[1])),
        bottom_right=(int(br[0]), int(br[1])),
        bottom_left=(int(bl[0]), int(bl[1])),
    )


def detect_slide_quad(image_bgr: ImageBGR) -> Quad | None:
    """检测幻灯片屏幕四边形；检测不到返回 None（不抛异常）。

    流程：灰度 → Otsu 阈值取亮区 → 最大外轮廓 → approxPolyDP 近似。
    仅当近似为恰好 4 个顶点、且面积占比达标时认为是幻灯片屏幕。
    """
    if image_bgr.size == 0:
        return None
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    # Otsu 自动阈值：LED 屏幕亮区 → 白，背景吊顶 / 观众 → 黑
    _, binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    contours, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    if len(contours) == 0:
        return None
    largest = max(contours, key=cv2.contourArea)
    img_area = float(image_bgr.shape[0] * image_bgr.shape[1])
    if cv2.contourArea(largest) < _MIN_AREA_RATIO * img_area:
        return None
    peri = cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, _APPROX_EPSILON_RATIO * peri, True)
    if approx.shape[0] != 4:
        return None
    return _order_corners(approx.reshape(4, 2).astype(np.float32))


def rectify(
    image_bgr: ImageBGR, quad: Quad, *, top_extend_ratio: float = 0.2,
) -> ImageBGR:
    """按四边形把屏摄图透视矫正为正视图。

    顶边上抬：屏摄常被吊顶 / 暗标题栏遮挡，把源四边形顶边沿"向上"方向
    外扩 top_extend_ratio 比例，使矫正结果纳入标题栏区域。
    """
    src = quad.as_array().copy()
    tl, tr, br, bl = src[0], src[1], src[2], src[3]
    # 左右两条竖边的"向上"向量（底 → 顶），用于把顶边外扩
    left_up = tl - bl
    right_up = tr - br
    src[0] = tl + left_up * top_extend_ratio   # 顶左外扩
    src[1] = tr + right_up * top_extend_ratio  # 顶右外扩

    # 目标矩形尺寸：宽取上下边最大长度，高取左右边最大长度（含上抬）
    width = max(_dist(tr, tl), _dist(br, bl))
    height = max(_dist(bl, tl), _dist(br, tr)) * (1.0 + top_extend_ratio)
    w, h = int(round(width)), int(round(height))
    if w <= 0 or h <= 0:
        return image_bgr
    dst = np.array(
        [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src.astype(np.float32), dst)
    warped: ImageBGR = cv2.warpPerspective(image_bgr, matrix, (w, h))
    return warped


def _rectify_sync(
    image_path: Path,
    output_dir: Path,
    *,
    save_debug: bool,
    debug_dir: str,
    top_extend_ratio: float,
) -> Path:
    """rectify_page 的同步实现（在 to_thread 中运行）。

    返回供下游 OCR 使用的图片路径：矫正成功 → 矫正图路径；任何失败 → 原图路径。
    """
    image = cv2.imread(str(image_path))
    if image is None:
        logger.warning("矫正读图失败，回退原图：%s", image_path)
        return image_path
    quad = detect_slide_quad(image)
    if quad is None:
        logger.info("未检测到幻灯片四边形，回退原图：%s", image_path.name)
        return image_path
    try:
        warped = rectify(image, quad, top_extend_ratio=top_extend_ratio)
    except cv2.error:
        logger.warning(
            "透视矫正异常，回退原图：%s", image_path.name, exc_info=True,
        )
        return image_path

    rectified_dir = output_dir / debug_dir
    rectified_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem
    suffix = image_path.suffix or ".jpg"
    after_path = rectified_dir / f"{stem}_after{suffix}"
    if not cv2.imwrite(str(after_path), warped):
        logger.warning("矫正图写盘失败，回退原图：%s", image_path.name)
        return image_path
    if save_debug:
        # before 对照：原图副本，便于与 after 目视对比验收
        cv2.imwrite(str(rectified_dir / f"{stem}_before{suffix}"), image)
    return after_path


async def rectify_page(
    image_path: Path,
    output_dir: Path,
    *,
    save_debug: bool = True,
    debug_dir: str = ".rectified",
    top_extend_ratio: float = 0.2,
) -> Path:
    """逐页矫正异步入口。OpenCV 阻塞调用走 to_thread，不阻塞事件循环。

    任何失败（读图失败 / 未检测到四边形 / 矫正异常 / 写盘失败）都回退原图
    路径，保证下游 OCR 不中断。返回供 OCR 使用的图片路径。
    """
    return await asyncio.to_thread(
        _rectify_sync,
        image_path,
        output_dir,
        save_debug=save_debug,
        debug_dir=debug_dir,
        top_extend_ratio=top_extend_ratio,
    )
