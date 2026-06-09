# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""content_crop 正文区左右边界检测单元测试。

用合成的「左导航 + 中正文 + 右大纲」三栏图验证算法排除侧栏；不依赖真实数据集路径。
cv2 缺失时整文件跳过（与 slide_rectify 测试一致）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

import cv2  # noqa: E402
from cv2.typing import MatLike  # noqa: E402

from docrestore.processing.content_crop import (  # noqa: E402
    _next_manual_figure_name,
    _runs,
    apply_crop_boxes,
    compute_crop_box,
    crop_page,
    crop_region_to_images,
    detect_boxes_for_dir,
    detect_content_lr,
)


def _lines(
    img: MatLike, x0: int, x1: int, y0: int, y1: int, gap: int, thick: int,
) -> None:
    """在 [x0,x1]×[y0,y1] 区域按 gap 画黑色横线，模拟一栏文字行。"""
    y = y0
    while y < y1:
        cv2.line(img, (x0, y), (x1, y), (40, 40, 40), thick)
        y += gap


def _make_three_column(w: int = 1280, h: int = 900) -> MatLike:
    """左导航[40,200] + 中正文[460,840] + 右大纲[1080,1240]，栏间宽沟壑。"""
    img = np.full((h, w, 3), 255, np.uint8)
    _lines(img, 40, 200, 80, 820, 30, 4)     # 左导航：窄列密集短行
    _lines(img, 460, 840, 100, 400, 36, 6)   # 中正文上半（宽行）
    _lines(img, 460, 780, 460, 800, 36, 6)   # 中正文下半（含段间空白）
    _lines(img, 1080, 1240, 90, 700, 32, 4)  # 右大纲：窄列短行
    return img


def _make_full_width(w: int = 1280, h: int = 900) -> MatLike:
    """无侧栏：正文铺满整宽（模拟已人工裁剪的图）。"""
    img = np.full((h, w, 3), 255, np.uint8)
    _lines(img, 60, 1220, 100, 800, 36, 6)
    return img


class TestRuns:
    """连续段提取工具。"""

    def test_basic(self) -> None:
        m = np.array([False, True, True, False, True], dtype=np.bool_)
        assert _runs(m) == [(1, 3), (4, 5)]

    def test_empty_and_full(self) -> None:
        assert _runs(np.zeros(5, dtype=np.bool_)) == []
        assert _runs(np.ones(4, dtype=np.bool_)) == [(0, 4)]


class TestDetectContentLR:
    """正文列左右边界检测。"""

    def test_three_column_excludes_sidebars(self) -> None:
        """三栏图：检测框排除左导航与右大纲，覆盖中正文。"""
        res = detect_content_lr(_make_three_column())
        assert res is not None
        x0, x1 = res
        assert 200 < x0 < 460   # 排除左导航(右缘200)，落在中正文左缘附近
        assert 840 < x1 < 1080  # 覆盖中正文(右缘840)，排除右大纲(左缘1080)

    def test_full_width_returns_near_full(self) -> None:
        """无侧栏图：检测框接近整宽（供 S2 判已裁剪→跳过）。"""
        res = detect_content_lr(_make_full_width())
        assert res is not None
        x0, x1 = res
        assert (x1 - x0) / 1280 > 0.9

    def test_empty_image_returns_none(self) -> None:
        assert detect_content_lr(np.zeros((0, 0, 3), dtype=np.uint8)) is None

    def test_blank_image_returns_none(self) -> None:
        """纯白无文字 → 无文本列 → None。"""
        assert detect_content_lr(np.full((400, 600, 3), 255, np.uint8)) is None


class TestComputeCropBox:
    """裁剪框 + 已裁剪 / 误检跳过判据。"""

    def test_three_column_returns_box(self) -> None:
        """有侧栏：返回排除侧栏的裁剪框。"""
        box = compute_crop_box(_make_three_column())
        assert box is not None
        x0, x1 = box
        assert x0 > 200
        assert x1 < 1080

    def test_full_width_skipped(self) -> None:
        """无侧栏（已裁剪 / 全屏）：框宽近整宽 → 跳过返回 None。"""
        assert compute_crop_box(_make_full_width()) is None

    def test_blank_skipped(self) -> None:
        """检测失败 → 跳过 None。"""
        assert compute_crop_box(np.full((400, 600, 3), 255, np.uint8)) is None


class TestCropPage:
    """crop_page 异步入口：裁剪落盘 / 失败 / 跳过都回退原图。"""

    @pytest.mark.asyncio
    async def test_three_column_crops(self, tmp_path: Path) -> None:
        """有侧栏：落盘裁剪图并返回其路径，裁剪图比原图窄。"""
        src = tmp_path / "doc.jpg"
        cv2.imwrite(str(src), _make_three_column())
        out = await crop_page(src, tmp_path, save_debug=False)
        assert out != src
        cropped = cv2.imread(str(out))
        assert cropped is not None
        assert cropped.shape[1] < 1280

    @pytest.mark.asyncio
    async def test_full_width_returns_original(self, tmp_path: Path) -> None:
        """无侧栏（已裁剪）：回退原图路径。"""
        src = tmp_path / "cropped.jpg"
        cv2.imwrite(str(src), _make_full_width())
        out = await crop_page(src, tmp_path, save_debug=False)
        assert out == src

    @pytest.mark.asyncio
    async def test_missing_file_returns_original(
        self, tmp_path: Path,
    ) -> None:
        """读图失败：回退原图路径。"""
        src = tmp_path / "nonexist.jpg"
        out = await crop_page(src, tmp_path, save_debug=False)
        assert out == src


class TestCropHelpers:
    """S4 后端 API 辅助：目录建议框检测 + 就地预裁剪。"""

    def test_detect_boxes_for_dir(self, tmp_path: Path) -> None:
        """有侧栏图给框（纵向整高）、无侧栏图给 None。"""
        cv2.imwrite(str(tmp_path / "threecol.jpg"), _make_three_column())
        cv2.imwrite(str(tmp_path / "fullwidth.jpg"), _make_full_width())
        by_name = {n: box for n, _w, _h, box in detect_boxes_for_dir(tmp_path)}
        assert by_name["fullwidth.jpg"] is None  # 无侧栏 → 跳过
        box = by_name["threecol.jpg"]
        assert box is not None
        assert box[1] == 0  # 纵向整高：y0=0
        assert box[3] == 900  # y1=h
        assert box[0] > 200  # 排除左导航
        assert box[2] < 1080  # 排除右大纲

    def test_apply_crop_boxes_inplace(self, tmp_path: Path) -> None:
        """就地按框裁剪、覆盖原图。"""
        src = tmp_path / "img.jpg"
        cv2.imwrite(str(src), _make_three_column())  # 1280x900
        apply_crop_boxes(tmp_path, {"img.jpg": (300, 0, 900, 900)})
        out = cv2.imread(str(src))
        assert out is not None
        assert out.shape[1] == 600  # 裁到 x[300,900]=600 宽
        assert out.shape[0] == 900  # 整高不变

    def test_apply_crop_boxes_skips_traversal(self, tmp_path: Path) -> None:
        """越界路径（../）被路径穿越守卫跳过，不处理外部文件。"""
        outside = tmp_path.parent / "docrestore_evil_probe.jpg"
        cv2.imwrite(str(outside), _make_three_column())
        try:
            apply_crop_boxes(
                tmp_path, {"../docrestore_evil_probe.jpg": (0, 0, 50, 50)},
            )
            out = cv2.imread(str(outside))
            assert out is not None
            assert out.shape[1] == 1280  # 未被裁剪
        finally:
            outside.unlink(missing_ok=True)


class TestCropRegionToImages:
    """编辑模式手动重截插图：从源图按框裁块存进 images/。"""

    def test_crops_region_and_saves(self, tmp_path: Path) -> None:
        """按框裁出子图存为 manual_1.jpg，尺寸=框宽高。"""
        src = tmp_path / "page.jpg"
        cv2.imwrite(str(src), _make_full_width())  # 1280x900
        images = tmp_path / "out" / "images"
        name = crop_region_to_images(src, images, (100, 50, 700, 450))
        assert name == "manual_1.jpg"
        saved = cv2.imread(str(images / name))
        assert saved is not None
        assert saved.shape[1] == 600  # x[100,700]
        assert saved.shape[0] == 400  # y[50,450]

    def test_sequential_names_increment(self, tmp_path: Path) -> None:
        """连续两次重截 → manual_1 / manual_2，不覆盖。"""
        src = tmp_path / "page.jpg"
        cv2.imwrite(str(src), _make_full_width())
        images = tmp_path / "images"
        n1 = crop_region_to_images(src, images, (0, 0, 100, 100))
        n2 = crop_region_to_images(src, images, (0, 0, 120, 120))
        assert n1 == "manual_1.jpg"
        assert n2 == "manual_2.jpg"
        assert (images / n1).is_file()
        assert (images / n2).is_file()

    def test_next_name_skips_existing_max(self, tmp_path: Path) -> None:
        """已存在 manual_5.jpg → 下一个序号从 6 起（取最大 +1）。"""
        images = tmp_path / "images"
        images.mkdir()
        (images / "manual_5.jpg").write_bytes(b"x")
        assert _next_manual_figure_name(images) == "manual_6.jpg"

    def test_clamps_out_of_bounds_box(self, tmp_path: Path) -> None:
        """框超出图边界 → 夹取到图内，仍产出有效图（不抛异常）。"""
        src = tmp_path / "page.jpg"
        cv2.imwrite(str(src), _make_full_width())  # 1280x900
        images = tmp_path / "images"
        name = crop_region_to_images(src, images, (-50, -50, 5000, 5000))
        saved = cv2.imread(str(images / name))
        assert saved is not None
        assert saved.shape[1] == 1280
        assert saved.shape[0] == 900

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        """源图不存在 → ValueError（显式动作失败要暴露，不静默回退）。"""
        with pytest.raises(ValueError, match="读图失败"):
            crop_region_to_images(
                tmp_path / "nope.jpg", tmp_path / "images", (0, 0, 10, 10),
            )
