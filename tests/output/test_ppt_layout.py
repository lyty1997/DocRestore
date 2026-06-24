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

"""PPT 版面 sidecar 单测（Phase-2b · subtask 2）。

纯函数：坐标变换（§5）、OCR 区域→sidecar 映射、序列化 round-trip、
fail-safe 反序列化、磁盘 I/O。断言从构造输入派生，不写死数据集关键词。
"""

from __future__ import annotations

from pathlib import Path

from docrestore.models import LayoutRegion
from docrestore.output.ppt_layout import (
    PPT_LAYOUT_FILENAME,
    PptLayout,
    PptLayoutPage,
    PptLayoutRegion,
    build_ppt_layout,
    compute_canvas_emu,
    from_dict,
    layout_region_from_ocr,
    load_ppt_layout,
    region_box_emu,
    resolve_output_image_ref,
    to_dict,
    write_ppt_layout,
)

_SLIDE_W = 12192000  # 与模块内 _SLIDE_W_EMU 对齐（13.333in 全宽）


# ── compute_canvas_emu：首页长宽比定画布 ──────────────────────


def test_canvas_16_9_first_page() -> None:
    """16:9 首页 → 画布高 = 宽 * 9/16。"""
    assert compute_canvas_emu((1920, 1080)) == (_SLIDE_W, 6858000)


def test_canvas_4_3_first_page() -> None:
    """4:3 首页 → 画布高 = 宽 * 3/4。"""
    assert compute_canvas_emu((1600, 1200)) == (_SLIDE_W, round(_SLIDE_W * 3 / 4))


def test_canvas_invalid_size_returns_none() -> None:
    """尺寸任一维 <= 0 → None。"""
    assert compute_canvas_emu((0, 1080)) is None
    assert compute_canvas_emu((1920, 0)) is None


# ── region_box_emu：letterbox 像素→EMU ────────────────────────


def test_region_box_no_letterbox_pure_scale() -> None:
    """画布与图同长宽比（无黑边）→ 纯等比缩放，偏移为 0。"""
    canvas = (_SLIDE_W, 6858000)
    image = (1920, 1080)
    scale = _SLIDE_W // 1920  # 6350，整除
    box = region_box_emu(canvas, image, (960, 540, 1920, 1080))
    assert box == (960 * scale, 540 * scale, 960 * scale, 540 * scale)


def test_region_box_full_image_fills_canvas() -> None:
    """整图 bbox 铺满画布。"""
    canvas = (_SLIDE_W, 6858000)
    box = region_box_emu(canvas, (1920, 1080), (0, 0, 1920, 1080))
    assert box == (0, 0, _SLIDE_W, 6858000)


def test_region_box_letterbox_adds_side_offset() -> None:
    """4:3 图铺进 16:9 画布 → 左右留黑边（off_x>0），上下贴边（off_y=0）。"""
    canvas = (_SLIDE_W, 6858000)
    image = (1600, 1200)  # 4:3
    box = region_box_emu(canvas, image, (0, 0, 1600, 1200))
    assert box is not None
    left, top, width, _height = box
    assert top == 0  # 高度方向恰好贴满，无上下偏移
    assert left > 0  # 宽度方向留黑边
    # 缩放后图宽 + 两侧黑边 = 画布宽
    assert left * 2 + width == _SLIDE_W


def test_region_box_invalid_bbox_returns_none() -> None:
    """零面积 / 逆序 bbox → None。"""
    canvas = (_SLIDE_W, 6858000)
    assert region_box_emu(canvas, (1920, 1080), (100, 100, 100, 200)) is None
    assert region_box_emu(canvas, (1920, 1080), (200, 100, 100, 200)) is None


def test_region_box_invalid_image_size_returns_none() -> None:
    """image_size 非法 → None。"""
    assert region_box_emu((_SLIDE_W, 6858000), (0, 0), (0, 0, 10, 10)) is None


def test_region_box_out_of_canvas_clamped() -> None:
    """越界 bbox（超出图）→ clamp 进画布，不溢出。"""
    canvas = (_SLIDE_W, 6858000)
    box = region_box_emu(canvas, (1920, 1080), (1900, 1060, 5000, 5000))
    assert box is not None
    left, top, width, height = box
    assert left + width <= _SLIDE_W
    assert top + height <= 6858000


# ── resolve_output_image_ref / layout_region_from_ocr ─────────


def test_resolve_output_image_ref_mirrors_renderer_naming() -> None:
    """images/N.ext + stem → images/{stem}_N.ext（镜像 Renderer 命名）。"""
    assert resolve_output_image_ref("slide_A", "images/3.jpg") == \
        "images/slide_A_3.jpg"
    assert resolve_output_image_ref("s", "") == ""


def test_layout_region_from_ocr_image_resolves_ref_clears_content() -> None:
    """图片区域：忽略传入 content，把 raw 引用映射成最终输出路径。"""
    ocr = LayoutRegion(
        bbox=(0, 0, 50, 50), label="image", content="", image_ref="images/0.jpg",
    )
    region = layout_region_from_ocr(ocr, stem="slide_A", content="忽略")
    assert region.content == ""
    assert region.image_ref == "images/slide_A_0.jpg"


def test_layout_region_from_ocr_text_keeps_content() -> None:
    """文字区域：保留调用方传入的（已脱敏）content，image_ref 为空。"""
    ocr = LayoutRegion(bbox=(0, 0, 50, 50), label="text", content="原文")
    region = layout_region_from_ocr(ocr, stem="slide_A", content="脱敏后")
    assert region.content == "脱敏后"
    assert region.image_ref == ""


# ── build_ppt_layout ──────────────────────────────────────────


def _text(content: str) -> PptLayoutRegion:
    return PptLayoutRegion(bbox=(0, 0, 10, 10), label="text", content=content)


def test_build_layout_canvas_from_first_page() -> None:
    """画布按首页长宽比定。"""
    layout = build_ppt_layout([
        ("a.jpg", (1920, 1080), [_text("一")]),
        ("b.jpg", (1600, 1200), [_text("二")]),
    ])
    assert layout is not None
    assert layout.slide_size_emu == (_SLIDE_W, 6858000)
    assert [p.filename for p in layout.pages] == ["a.jpg", "b.jpg"]


def test_build_layout_no_regions_returns_none() -> None:
    """所有页都无区域（非 VL 引擎）→ None。"""
    assert build_ppt_layout([("a.jpg", (1920, 1080), [])]) is None


def test_build_layout_skips_invalid_first_size_for_canvas() -> None:
    """首页尺寸非法 → 画布回退到首个有效页。"""
    layout = build_ppt_layout([
        ("a.jpg", (0, 0), [_text("一")]),
        ("b.jpg", (1920, 1080), [_text("二")]),
    ])
    assert layout is not None
    assert layout.slide_size_emu == (_SLIDE_W, 6858000)


# ── 序列化 round-trip + fail-safe ─────────────────────────────


def test_to_from_dict_roundtrip() -> None:
    """to_dict → from_dict 恒等。"""
    layout = PptLayout(
        slide_size_emu=(_SLIDE_W, 6858000),
        pages=[
            PptLayoutPage(
                filename="a.jpg",
                image_size=(1920, 1080),
                regions=[
                    PptLayoutRegion((0, 0, 100, 30), "paragraph_title", "标题"),
                    PptLayoutRegion(
                        (0, 40, 200, 300), "image", "",
                        image_ref="images/a_0.jpg",
                    ),
                ],
            ),
        ],
    )
    assert from_dict(to_dict(layout)) == layout


def test_from_dict_wrong_version_returns_none() -> None:
    """版本不符 → None（fail-safe）。"""
    assert from_dict({"version": 999, "slide_size_emu": [1, 1], "pages": []}) \
        is None


def test_from_dict_malformed_returns_none() -> None:
    """非 dict / 缺字段 / bbox 非法 → None。"""
    assert from_dict("not a dict") is None
    assert from_dict({"version": 1, "pages": []}) is None  # 缺 slide_size_emu
    bad_bbox = {
        "version": 1,
        "slide_size_emu": [_SLIDE_W, 6858000],
        "pages": [{
            "filename": "a.jpg",
            "image_size": [1920, 1080],
            "regions": [{"bbox": [0, 0, 10], "label": "text", "content": "x"}],
        }],
    }
    assert from_dict(bad_bbox) is None


# ── 磁盘 I/O ──────────────────────────────────────────────────


def test_write_then_load_roundtrip(tmp_path: Path) -> None:
    """write → load 恒等。"""
    layout = PptLayout(
        slide_size_emu=(_SLIDE_W, 6858000),
        pages=[PptLayoutPage("a.jpg", (1920, 1080), [_text("内容")])],
    )
    path = write_ppt_layout(tmp_path, layout)
    assert path.name == PPT_LAYOUT_FILENAME
    assert load_ppt_layout(tmp_path) == layout


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """sidecar 缺失 → None。"""
    assert load_ppt_layout(tmp_path) is None


def test_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    """sidecar JSON 损坏 → None（不抛错）。"""
    (tmp_path / PPT_LAYOUT_FILENAME).write_text("{ broken", encoding="utf-8")
    assert load_ppt_layout(tmp_path) is None


# ── 颜色字段（§11）：透传 / round-trip / 向后兼容 / 降级 ────────


def test_layout_region_from_ocr_text_threads_colors() -> None:
    """文字区域：透传捕获期采样的前景 / 背景色。"""
    ocr = LayoutRegion(
        bbox=(0, 0, 50, 50), label="text", content="原文",
        fg_color=(10, 20, 30), bg_color=(200, 210, 220),
    )
    region = layout_region_from_ocr(ocr, stem="s", content="脱敏后")
    assert region.fg_color == (10, 20, 30)
    assert region.bg_color == (200, 210, 220)


def test_layout_region_from_ocr_image_has_no_colors() -> None:
    """图片区域：颜色无意义，恒为 None（即便 OCR 区域误带色）。"""
    ocr = LayoutRegion(
        bbox=(0, 0, 50, 50), label="image", content="", image_ref="images/0.jpg",
        fg_color=(10, 20, 30), bg_color=(200, 210, 220),
    )
    region = layout_region_from_ocr(ocr, stem="s", content="")
    assert region.fg_color is None
    assert region.bg_color is None


def test_to_from_dict_roundtrip_with_colors() -> None:
    """带颜色字段 to_dict → from_dict 恒等。"""
    region = PptLayoutRegion(
        (0, 0, 100, 30), "paragraph_title", "标题",
        fg_color=(240, 240, 250), bg_color=(20, 30, 80),
    )
    layout = PptLayout(
        slide_size_emu=(_SLIDE_W, 6858000),
        pages=[PptLayoutPage("a.jpg", (1920, 1080), [region])],
    )
    assert from_dict(to_dict(layout)) == layout


def _one_region_dict(region: dict[str, object]) -> dict[str, object]:
    """包一层合法的单页单区域 sidecar dict。"""
    return {
        "version": 1,
        "slide_size_emu": [_SLIDE_W, 6858000],
        "pages": [{
            "filename": "a.jpg",
            "image_size": [1920, 1080],
            "regions": [region],
        }],
    }


def test_from_dict_missing_color_keys_backward_compatible() -> None:
    """旧无色 sidecar（缺 fg_color/bg_color 键）→ 读成 None，不退整页。"""
    data = _one_region_dict({"bbox": [0, 0, 10, 10], "label": "text", "content": "x"})
    layout = from_dict(data)
    assert layout is not None
    region = layout.pages[0].regions[0]
    assert region.fg_color is None
    assert region.bg_color is None


def test_from_dict_invalid_color_degrades_to_none() -> None:
    """非法颜色值（长度 / 越界）→ 降级 None，核心字段仍解析、不退整页。"""
    data = _one_region_dict({
        "bbox": [0, 0, 10, 10], "label": "text", "content": "x",
        "fg_color": [1, 2],  # 长度非法
        "bg_color": [300, 0, 0],  # 越界
    })
    layout = from_dict(data)
    assert layout is not None
    region = layout.pages[0].regions[0]
    assert region.content == "x"  # 核心字段不受影响
    assert region.fg_color is None
    assert region.bg_color is None
