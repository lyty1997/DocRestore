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

"""版面区域捕获单测（PPT 版面定位导出 Phase-2b · subtask 1）。

纯函数测试：从 worker 的 coordinates + raw_text 构造 LayoutRegion，
覆盖文字类原文保留、image/chart 按阅读序认领图片引用、非法输入 fail-safe。
所有输入均为测试内构造（合成数据），断言从构造输入派生，不写死数据集关键词。
"""

from __future__ import annotations

from docrestore.models import LayoutRegion
from docrestore.ocr.paddle_ocr import (
    _build_layout_regions,
    _coerce_bbox,
    _iter_image_refs,
)


# ── _iter_image_refs：阅读序提取图片 src ──────────────────────


def test_iter_image_refs_preserves_interleaved_order() -> None:
    """markdown 与 HTML 图片交错出现时，按文档阅读序返回 src。"""
    raw = (
        "段落一\n"
        '<img src="images/2.jpg" />\n'
        "段落二\n"
        "![alt](images/0.jpg)\n"
        '<img alt="x" src="images/1.jpg">\n'
    )
    assert _iter_image_refs(raw) == [
        "images/2.jpg",
        "images/0.jpg",
        "images/1.jpg",
    ]


def test_iter_image_refs_empty_when_no_images() -> None:
    """无图片引用时返回空列表。"""
    assert _iter_image_refs("纯文字\n第二行") == []


# ── _coerce_bbox：bbox 收敛 ───────────────────────────────────


def test_coerce_bbox_valid_ints() -> None:
    """合法四元组原样收敛。"""
    assert _coerce_bbox([10, 20, 30, 40]) == (10, 20, 30, 40)


def test_coerce_bbox_truncates_floats() -> None:
    """浮点像素截断为整数。"""
    assert _coerce_bbox([10.9, 20.0, 30, 40]) == (10, 20, 30, 40)


def test_coerce_bbox_rejects_wrong_length() -> None:
    """长度不为 4 返回 None。"""
    assert _coerce_bbox([10, 20, 30]) is None


def test_coerce_bbox_rejects_non_list() -> None:
    """非 list 返回 None。"""
    assert _coerce_bbox("10,20,30,40") is None
    assert _coerce_bbox(None) is None


def test_coerce_bbox_rejects_non_numeric() -> None:
    """含非数值元素返回 None。"""
    assert _coerce_bbox([10, 20, "x", 40]) is None


# ── _build_layout_regions：构造版面区域 ───────────────────────


def test_text_region_keeps_content_no_image_ref() -> None:
    """文字类区域保留 block 文字，不认领图片。"""
    coords = [
        {"label": "paragraph_title", "bbox": [0, 0, 100, 30], "text": "标题文字"},
        {"label": "text", "bbox": [0, 40, 100, 200], "text": "正文内容"},
    ]
    regions = _build_layout_regions(coords, raw_text="")
    assert [r.label for r in regions] == ["paragraph_title", "text"]
    assert regions[0].content == "标题文字"
    assert regions[1].content == "正文内容"
    assert all(r.image_ref == "" for r in regions)


def test_image_and_chart_claim_refs_by_reading_order() -> None:
    """image/chart 区域按阅读序认领 raw_text 的图片引用，content 清空。"""
    raw = '<img src="images/1.jpg">\n<img src="images/0.jpg">'
    coords = [
        {"label": "image", "bbox": [0, 0, 50, 50], "text": ""},
        {"label": "chart", "bbox": [0, 60, 50, 110], "text": ""},
    ]
    regions = _build_layout_regions(coords, raw_text=raw)
    # 区域阅读序 image→chart 对应 raw_text 阅读序 1.jpg→0.jpg
    assert regions[0].label == "image"
    assert regions[0].image_ref == "images/1.jpg"
    assert regions[0].content == ""
    assert regions[1].label == "chart"
    assert regions[1].image_ref == "images/0.jpg"


def test_text_between_images_does_not_consume_ref() -> None:
    """文字区域夹在图片区域间，不消费图片引用游标。"""
    raw = "![](images/0.jpg)\n![](images/1.jpg)"
    coords = [
        {"label": "image", "bbox": [0, 0, 50, 50], "text": ""},
        {"label": "text", "bbox": [0, 60, 50, 110], "text": "夹层文字"},
        {"label": "image", "bbox": [0, 120, 50, 170], "text": ""},
    ]
    regions = _build_layout_regions(coords, raw_text=raw)
    assert regions[0].image_ref == "images/0.jpg"
    assert regions[1].image_ref == ""
    assert regions[2].image_ref == "images/1.jpg"


def test_more_image_regions_than_refs_get_empty_ref() -> None:
    """图片区域多于 raw_text 引用时，多出者 image_ref 为空（fail-safe）。"""
    raw = "![](images/0.jpg)"
    coords = [
        {"label": "image", "bbox": [0, 0, 50, 50], "text": ""},
        {"label": "image", "bbox": [0, 60, 50, 110], "text": ""},
    ]
    regions = _build_layout_regions(coords, raw_text=raw)
    assert regions[0].image_ref == "images/0.jpg"
    assert regions[1].image_ref == ""


def test_malformed_bbox_region_skipped() -> None:
    """bbox 非法的区域被跳过，其余照常构造。"""
    coords = [
        {"label": "text", "bbox": [0, 0, 100], "text": "缺一维"},
        {"label": "text", "bbox": [0, 40, 100, 200], "text": "正常"},
    ]
    regions = _build_layout_regions(coords, raw_text="")
    assert len(regions) == 1
    assert regions[0].content == "正常"


def test_non_dict_item_skipped() -> None:
    """coordinates 里的非 dict 元素被跳过。"""
    coords = ["噪声", {"label": "text", "bbox": [0, 0, 10, 10], "text": "有效"}]
    regions = _build_layout_regions(coords, raw_text="")
    assert len(regions) == 1
    assert regions[0].content == "有效"


def test_non_list_coordinates_returns_empty() -> None:
    """coordinates 非 list（非 VL 引擎/缺失）返回空列表。"""
    assert _build_layout_regions(None, raw_text="") == []
    assert _build_layout_regions({}, raw_text="any") == []


def test_default_label_when_missing() -> None:
    """缺 label 字段时回退 'text'，作为文字类保留 content。"""
    coords = [{"bbox": [0, 0, 10, 10], "text": "无标签"}]
    regions = _build_layout_regions(coords, raw_text="")
    assert regions == [
        LayoutRegion(bbox=(0, 0, 10, 10), label="text", content="无标签"),
    ]
