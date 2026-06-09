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

import asyncio
import logging
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike
from numpy.typing import NDArray

from docrestore.processing.slide_rectify import Quad, warp_quad

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


def _crop_sync(
    image_path: Path,
    output_dir: Path,
    *,
    save_debug: bool,
    debug_dir: str,
) -> Path:
    """``crop_page`` 的同步实现（在 to_thread 中运行）。

    返回供下游 OCR 使用的图片路径：裁剪成功 → 裁剪图路径；任何失败 / 跳过 → 原图路径。
    """
    image = cv2.imread(str(image_path))
    if image is None:
        logger.warning("正文裁剪读图失败，回退原图：%s", image_path)
        return image_path
    box = compute_crop_box(image)
    if box is None:
        logger.info(
            "未检测到可裁剪正文区（已裁剪 / 无侧栏 / 检测不可靠），回退原图：%s",
            image_path.name,
        )
        return image_path
    x0, x1 = box
    cropped = image[:, x0:x1]  # MVP：纵向取整高
    crop_dir = output_dir / debug_dir
    stem = image_path.stem
    suffix = image_path.suffix or ".jpg"
    after_path = crop_dir / f"{stem}_crop{suffix}"
    # 落盘段整体兜底：mkdir / imwrite 可能抛 OSError 或 cv2.error，统一回退原图，
    # 兑现"任何失败回退原图、不中断下游 OCR"契约（与 slide_rectify 一致）。
    try:
        crop_dir.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(after_path), cropped):
            logger.warning("裁剪图写盘失败，回退原图：%s", image_path.name)
            return image_path
        if save_debug:
            vis = image.copy()
            cv2.rectangle(
                vis, (x0, 0), (x1, image.shape[0] - 1), (0, 0, 255), 6,
            )
            cv2.imwrite(str(crop_dir / f"{stem}_box{suffix}"), vis)
    except (OSError, cv2.error):
        logger.warning(
            "裁剪图落盘异常，回退原图：%s", image_path.name, exc_info=True,
        )
        return image_path
    return after_path


async def crop_page(
    image_path: Path,
    output_dir: Path,
    *,
    save_debug: bool = True,
    debug_dir: str = ".content_crop",
) -> Path:
    """逐页正文裁剪异步入口。OpenCV 阻塞调用走 to_thread，不阻塞事件循环。

    任何失败（读图失败 / 检测不可靠 / 已裁剪跳过 / 写盘失败）都回退原图路径，
    保证下游 OCR 不中断。返回供 OCR 使用的图片路径。
    """
    return await asyncio.to_thread(
        _crop_sync,
        image_path,
        output_dir,
        save_debug=save_debug,
        debug_dir=debug_dir,
    )


#: 支持的图片扩展名（小写比较，兼容 .JPG/.jpg）。
_IMAGE_EXTS = (".jpg", ".jpeg", ".png")
#: 检测框 (x0, y0, x1, y1)，原图像素坐标系。
CropBoxTuple = tuple[int, int, int, int]
#: 四角点 (左上, 右上, 右下, 左下)，每点 (x, y)，原图像素坐标系。顺序即角色，
#: 由调用方（前端固定角色手柄）保证，供四角透视校正。
QuadTuple = tuple[
    tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int],
]


class DegenerateQuadError(Exception):
    """四角校正的四边形退化（近共线 sliver），无法透视矫正。

    与"读图失败"区分：让路由把它映射成 400（区域无效）而非 404（源图不存在）。
    """


def detect_boxes_for_dir(
    image_dir: Path,
) -> list[tuple[str, int, int, CropBoxTuple | None]]:
    """对 image_dir 下每张图检测建议正文框（MVP 纵向整高）。

    返回 ``[(相对名, 宽, 高, (x0,y0,x1,y1) | None), ...]``；box=None 表示无需裁剪
    （已裁剪 / 无侧栏 / 检测失败）。供前端"裁剪预览 + 拖拽微调"取建议框。
    """
    items: list[tuple[str, int, int, CropBoxTuple | None]] = []
    for p in sorted(image_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _IMAGE_EXTS:
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        box = compute_crop_box(img)
        rel = str(p.relative_to(image_dir))
        items.append(
            (rel, w, h, None if box is None else (box[0], 0, box[1], h)),
        )
    return items


#: 手动重截插图的文件名前缀（区别于 OCR 自动抽取的 ``{stem}_{N}``）。
_MANUAL_FIGURE_PREFIX = "manual_"


def _next_manual_figure_name(images_dir: Path) -> str:
    """扫描 images_dir 下现存 ``manual_N.jpg``，返回下一个空闲序号文件名。

    取最大现存序号 +1，避免覆盖 OCR 自动抽取的图或已有的手动截图。
    目录不存在视为无现存手动图（返回 ``manual_1.jpg``）。
    """
    max_n = 0
    if images_dir.is_dir():
        for p in images_dir.glob(f"{_MANUAL_FIGURE_PREFIX}*.jpg"):
            stem = p.stem[len(_MANUAL_FIGURE_PREFIX):]
            if stem.isdigit():
                max_n = max(max_n, int(stem))
    return f"{_MANUAL_FIGURE_PREFIX}{max_n + 1}.jpg"


def crop_region_to_images(
    source_image: Path,
    images_dir: Path,
    box: CropBoxTuple,
) -> str:
    """从 source_image 按 box 裁一块，存进 images_dir，返回生成的文件名。

    供编辑模式"手动重截插图"：用户框选源图某区域 → 裁出 → 存为 ``manual_N.jpg``。
    box 越界自动夹取到图内有效范围。读图失败抛 ``ValueError``，写盘失败抛 ``OSError``，
    由调用方转成 API 错误（本函数不吞异常，与逐页裁剪"失败回退原图"语义不同——
    手动重截是显式用户动作，失败要让用户看到，不能静默）。
    """
    img = cv2.imread(str(source_image))
    if img is None:
        raise ValueError(f"读图失败: {source_image}")
    h, w = img.shape[:2]
    x0 = max(0, min(box[0], w - 1))
    y0 = max(0, min(box[1], h - 1))
    x1 = max(x0 + 1, min(box[2], w))
    y1 = max(y0 + 1, min(box[3], h))
    cropped = img[y0:y1, x0:x1]
    images_dir.mkdir(parents=True, exist_ok=True)
    name = _next_manual_figure_name(images_dir)
    out_path = images_dir / name
    if not cv2.imwrite(str(out_path), cropped):
        raise OSError(f"裁剪图写盘失败: {out_path}")
    return name


def crop_quad_to_images(
    source_image: Path,
    images_dir: Path,
    quad: QuadTuple,
) -> str:
    """从 source_image 按四角点透视矫正裁一块，存进 images_dir，返回文件名。

    供编辑模式"四角校正"：用户在源图上放 4 个角点（左上 / 右上 / 右下 / 左下）
    框住倾斜 / 透视变形的插图 → 透视变换矫正为正视矩形 → 存为 ``manual_N.jpg``。
    角点先夹取到图内（越界点会让透视矩阵异常）；**按给定角色顺序信任、不按几何
    重排**（否则旋转的插图会被重标角点而矫正成错向）。

    与 ``crop_region_to_images`` 同属显式用户动作：读图失败 / 退化四边形抛
    ``ValueError``、写盘失败抛 ``OSError``，由调用方转 API 错误（不静默回退）。
    """
    img = cv2.imread(str(source_image))
    if img is None:
        raise ValueError(f"读图失败: {source_image}")
    h, w = img.shape[:2]

    def _clamp(point: tuple[int, int]) -> tuple[int, int]:
        return (max(0, min(point[0], w - 1)), max(0, min(point[1], h - 1)))

    q = Quad(
        top_left=_clamp(quad[0]),
        top_right=_clamp(quad[1]),
        bottom_right=_clamp(quad[2]),
        bottom_left=_clamp(quad[3]),
    )
    warped = warp_quad(img, q)
    if warped is None:
        raise DegenerateQuadError(f"退化四边形，无法矫正: {quad}")
    images_dir.mkdir(parents=True, exist_ok=True)
    name = _next_manual_figure_name(images_dir)
    out_path = images_dir / name
    if not cv2.imwrite(str(out_path), warped):
        raise OSError(f"裁剪图写盘失败: {out_path}")
    return name


def apply_crop_boxes(
    image_dir: Path,
    boxes: dict[str, CropBoxTuple],
) -> None:
    """按 boxes（图名 → (x0,y0,x1,y1)）**就地**裁剪 image_dir 的图（覆盖原图）。

    只处理 boxes 里有框的图（裁剪后覆盖写回），其余图不动。裁剪后的图 content_crop
    自动检测会判为"已裁剪"跳过、不二次裁。读图失败 / 越界路径的图保持原样不动。
    """
    root = image_dir.resolve()
    for rel, box in boxes.items():
        p = (image_dir / rel).resolve()
        # 路径穿越防护：必须落在 image_dir 内
        if root not in p.parents:
            continue
        if not p.is_file() or p.suffix.lower() not in _IMAGE_EXTS:
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        x0 = max(0, min(box[0], w - 1))
        y0 = max(0, min(box[1], h - 1))
        x1 = max(x0 + 1, min(box[2], w))
        y1 = max(y0 + 1, min(box[3], h))
        cv2.imwrite(str(p), img[y0:y1, x0:x1])
