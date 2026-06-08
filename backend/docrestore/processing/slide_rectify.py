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
from typing import TypeAlias

import cv2
import numpy as np
from cv2.typing import MatLike
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

#: OpenCV 图像类型别名（BGR 通道）。用 cv2 官方 MatLike 以匹配 cv2 函数签名
#: （opencv-python-headless 自带 stub，函数返回非 uint8 的宽泛 ndarray 类型）。
ImageBGR: TypeAlias = MatLike

#: approxPolyDP epsilon 相对周长比例：值越大越激进地把轮廓简化成少边形
_APPROX_EPSILON_RATIO = 0.02
#: 候选四边形面积至少占全图比例，过滤掉小亮斑（反光点 / logo）
_MIN_AREA_RATIO = 0.2
#: 矫正结果任一边的最小像素数。低于此视为退化四边形（近共线 sliver / 反光条），
#: 透视变换会产出 1×N 之类的"竹签图"喂给 OCR；此时回退原图。正常幻灯片矫正
#: 结果都是数百像素，16px 既能拦下退化 sliver，又绝不会误伤真实幻灯片。
_MIN_RECTIFIED_SIDE_PX = 16
#: 四边形偏离正矩形的"偏斜"阈值（度）：4 角偏离直角的最大值 ≤ 此值视为近正视。
#: 近正视幻灯片只**遮黑周边、保持原图尺寸**、不做透视 warp（warp / 裁小都会改变
#: 幻灯片在画面里的相对尺度，让下游 VL 把化学结构 / 图表切碎）；超过此值的强透视
#: 屏摄才做完整 warp 矫正。
_MAX_SKEW_DEG = 8.0


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

    先按 y 排序分上下两组（y 最小的两点是上边、最大的两点是下边），组内再
    按 x 分左右：上组左=左上、右=右上，下组左=左下、右=右下。每点恰好分配
    一次 ⇒ 4 角必互异——既避免旧的"x+y 最小=左上 / y-x 最小=右上"启发式
    把同一点同时判成两角而塌缩（→ getPerspectiveTransform 奇异矩阵、产出乱
    图），也修掉"极角排环 + x+y 锚点"在旋转 / 强倾斜下把左上锚错位、整圈标号
    偏移一格的问题（曾让矫正图整体旋转 90°）。

    该法对透视梯形（keystone，屏摄主畸变）天然稳健，对中等旋转也不误标；
    仅当旋转大到使某下角的 y 升过某上角才会失准（正常握持拍摄不会到此程度，
    且那种朝向无法仅凭几何恢复）。
    """
    by_y = pts[np.argsort(pts[:, 1], kind="stable")]
    top = by_y[:2]
    bottom = by_y[2:]
    tl, tr = top[np.argsort(top[:, 0], kind="stable")]
    bl, br = bottom[np.argsort(bottom[:, 0], kind="stable")]
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
    src = quad.as_array()
    # 取原始角点副本：下面要原地外扩 src[0]/src[1]，而 tl/tr/br/bl 必须保持
    # 原值用于测距——它们若是 src 的视图，被外扩后 _dist(bl, tl) 会把上抬量算进
    # 边长，再乘 (1+ratio) 等于重复放大，矫正图竖向被多拉伸 (1+ratio) 倍。
    tl, tr, br, bl = (
        src[0].copy(), src[1].copy(), src[2].copy(), src[3].copy(),
    )
    # 左右两条竖边的"向上"向量（底 → 顶），用于把顶边外扩
    left_up = tl - bl
    right_up = tr - br
    src[0] = tl + left_up * top_extend_ratio   # 顶左外扩
    src[1] = tr + right_up * top_extend_ratio  # 顶右外扩

    # 目标矩形尺寸：宽取上下边最大长度（原始角点）；高取左右边原始最大长度，
    # 按上抬比例放大一次（= 外扩后源四边形的竖向跨度），不再重复乘。
    width = max(_dist(tr, tl), _dist(br, bl))
    height = max(_dist(bl, tl), _dist(br, tr)) * (1.0 + top_extend_ratio)
    w, h = int(round(width)), int(round(height))
    # 退化四边形（近共线 sliver）会算出极小的一边 → 透视变换出竹签图喂坏 OCR；
    # 任一边低于阈值即回退原图（与"检测不到四边形回退原图"同一契约）。
    if w < _MIN_RECTIFIED_SIDE_PX or h < _MIN_RECTIFIED_SIDE_PX:
        return image_bgr
    dst = np.array(
        [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src.astype(np.float32), dst)
    warped: ImageBGR = cv2.warpPerspective(image_bgr, matrix, (w, h))
    return warped


def _quad_skew(quad: Quad) -> float:
    """四边形偏离正矩形的程度：4 个角偏离直角(90°)的最大度数。

    近正视屏摄 → 各角接近 90°、返回值小；强透视 → 角偏离大、返回值大。
    """
    pts = quad.as_array()
    tl, tr, br, bl = pts[0], pts[1], pts[2], pts[3]

    def corner(
        a: NDArray[np.float32], b: NDArray[np.float32], c: NDArray[np.float32],
    ) -> float:
        v1, v2 = a - b, c - b
        denom = float(np.linalg.norm(v1) * np.linalg.norm(v2)) + 1e-9
        cos = float(np.dot(v1, v2)) / denom
        return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))

    angles = [
        corner(bl, tl, tr), corner(tl, tr, br),
        corner(tr, br, bl), corner(br, bl, tl),
    ]
    return max(abs(a - 90.0) for a in angles)


def _mask_surroundings(
    image_bgr: ImageBGR, quad: Quad, *, top_extend_ratio: float,
) -> ImageBGR:
    """去掉屏幕四边形外的周边（投影厅 / 墙面等），但**保持原图尺寸**：把包围盒外
    涂黑、不裁小、不缩放、不 warp。

    关键：实测 VL 对"幻灯片占满画面"的小图会按更高分辨率把化学结构 / 图表切碎；
    若直接裁小（或 warp 缩小），幻灯片相对尺度被放大 → 切碎。这里只 mask 周边、
    保持幻灯片在原图中的位置与尺度不变，VL 的内部缩放与处理整张原图时一致 →
    图保持完整；同时周边被涂黑，不污染 OCR。
    """
    pts = quad.as_array()
    h, w = image_bgr.shape[:2]
    x0 = int(max(0.0, float(np.min(pts[:, 0]))))
    x1 = int(min(float(w), float(np.max(pts[:, 0]))))
    y0 = int(max(0.0, float(np.min(pts[:, 1]))))
    y1 = int(min(float(h), float(np.max(pts[:, 1]))))
    # 顶边上抬，补回常被暗标题栏 / 吊顶遮挡的区域
    box_h = y1 - y0
    y0 = int(max(0, y0 - int(box_h * top_extend_ratio)))
    if x1 - x0 < _MIN_RECTIFIED_SIDE_PX or y1 - y0 < _MIN_RECTIFIED_SIDE_PX:
        return image_bgr
    masked: ImageBGR = np.zeros_like(image_bgr)
    masked[y0:y1, x0:x1] = image_bgr[y0:y1, x0:x1]
    return masked


def _rectify_sync(
    image_path: Path,
    output_dir: Path,
    *,
    save_debug: bool,
    debug_dir: str,
    top_extend_ratio: float,
    max_skew_deg: float,
) -> Path:
    """rectify_page 的同步实现（在 to_thread 中运行）。

    返回供下游 OCR 使用的图片路径：处理成功 → 处理图路径；任何失败 → 原图路径。

    **按需矫正**：检测到的四边形偏斜 ≤ ``max_skew_deg`` 视为近正视，只按包围盒
    裁掉周边、不做透视 warp（保完整图，避免 VL 切碎）；超过则做完整 warp 矫正。
    两条路都"裁掉周边"，区别仅在要不要 warp。
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
        skew = _quad_skew(quad)
        if skew <= max_skew_deg:
            out_img = _mask_surroundings(
                image, quad, top_extend_ratio=top_extend_ratio,
            )
            logger.info(
                "幻灯片近正视(偏斜%.1f°≤%.1f°)，仅遮黑周边保原尺寸、不 warp：%s",
                skew, max_skew_deg, image_path.name,
            )
        else:
            out_img = rectify(image, quad, top_extend_ratio=top_extend_ratio)
            logger.info(
                "幻灯片强透视(偏斜%.1f°>%.1f°)，完整透视矫正：%s",
                skew, max_skew_deg, image_path.name,
            )
    except cv2.error:
        logger.warning(
            "矫正 / 裁剪异常，回退原图：%s", image_path.name, exc_info=True,
        )
        return image_path

    rectified_dir = output_dir / debug_dir
    stem = image_path.stem
    suffix = image_path.suffix or ".jpg"
    after_path = rectified_dir / f"{stem}_after{suffix}"
    # 落盘段整体兜底：mkdir / imwrite 可能抛 OSError（只读目录、磁盘满、父级是
    # 文件）或 cv2.error（编码失败），统一回退原图，兑现"任何失败回退原图、
    # 不中断下游 OCR"契约（否则异常会冒泡到 _ocr_producer 崩整个 task）。
    try:
        rectified_dir.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(after_path), out_img):
            logger.warning("处理图写盘失败，回退原图：%s", image_path.name)
            return image_path
        if save_debug:
            # before 对照：原图副本，便于与 after 目视对比验收
            cv2.imwrite(str(rectified_dir / f"{stem}_before{suffix}"), image)
    except (OSError, cv2.error):
        logger.warning(
            "矫正图落盘异常，回退原图：%s", image_path.name, exc_info=True,
        )
        return image_path
    return after_path


async def rectify_page(
    image_path: Path,
    output_dir: Path,
    *,
    save_debug: bool = True,
    debug_dir: str = ".rectified",
    top_extend_ratio: float = 0.2,
    max_skew_deg: float = _MAX_SKEW_DEG,
) -> Path:
    """逐页矫正异步入口。OpenCV 阻塞调用走 to_thread，不阻塞事件循环。

    ``max_skew_deg``：四边形偏斜 ≤ 此值（度）只裁剪去周边、不做透视 warp（保完整图）；
    超过才完整 warp 矫正。任何失败（读图 / 未检测到四边形 / 处理异常 / 写盘）都回退
    原图路径，保证下游 OCR 不中断。返回供 OCR 使用的图片路径。
    """
    return await asyncio.to_thread(
        _rectify_sync,
        image_path,
        output_dir,
        save_debug=save_debug,
        debug_dir=debug_dir,
        top_extend_ratio=top_extend_ratio,
        max_skew_deg=max_skew_deg,
    )
