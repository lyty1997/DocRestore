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

"""PPT 版面定位 sidecar：位置真相源 ``.ppt_layout.json`` + 坐标变换（§5）。

PPT 版面定位导出（Epic D Phase-2b）的位置真相源：``_ppt_pipeline`` 落盘、
``pptx`` 导出器读取按 bbox 定位渲染。本模块纯函数（坐标变换 + 序列化），
不依赖 OCR/PII/导出工具，便于单测。设计真相源 ``docs/zh/ppt-layout-export.md``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from docrestore.models import LayoutRegion
from docrestore.output.sidecar_common import (
    as_int_pair,
    as_int_quad,
    load_json_sidecar,
    write_json_sidecar,
)

#: sidecar 文件名：dot 前缀=内部文件，不进下载 zip / asset 白名单。
PPT_LAYOUT_FILENAME = ".ppt_layout.json"
#: schema 版本：导出器读到不匹配版本即退竖排（fail-safe）。
_LAYOUT_VERSION = 1
#: pptx 画布固定宽 13.333in（16:9 全宽 EMU），高按首页长宽比定（§5 决策1）。
_SLIDE_W_EMU = 12192000


@dataclass(frozen=True)
class PptLayoutRegion:
    """版面区域：像素 bbox + 类型 + 内容/裁图引用（最终输出相对路径）。"""

    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) 像素（落在 image_size 内）
    label: str  # paragraph_title/text/figure_title/table/image/chart
    content: str = ""  # 文字类=文字/HTML 表；image/chart=空（见 image_ref）
    image_ref: str = ""  # image/chart：最终输出相对引用 images/{stem}_N.ext
    fg_color: tuple[int, int, int] | None = None  # 前景(文字)色，§11；None=退默认黑
    bg_color: tuple[int, int, int] | None = None  # 背景色，§11；None=不填充


@dataclass(frozen=True)
class PptLayoutPage:
    """单页版面：原图文件名 + 像素尺寸（bbox 坐标空间）+ 区域列表。"""

    filename: str
    image_size: tuple[int, int]  # (width, height) 像素
    regions: list[PptLayoutRegion] = field(default_factory=list)


@dataclass(frozen=True)
class PptLayout:
    """整份 PPT 版面：画布 EMU 尺寸 + 各页区域。"""

    slide_size_emu: tuple[int, int]  # 画布 (width, height) EMU
    pages: list[PptLayoutPage]
    version: int = _LAYOUT_VERSION


# ── 坐标变换（§5）：像素 bbox → slide EMU ─────────────────────


def compute_canvas_emu(image_size: tuple[int, int]) -> tuple[int, int] | None:
    """首页长宽比定画布：宽固定 ``_SLIDE_W_EMU``，高按比例。

    image_size 非法（任一维 <= 0）→ None（调用方退竖排）。
    """
    w, h = image_size
    if w <= 0 or h <= 0:
        return None
    return (_SLIDE_W_EMU, round(_SLIDE_W_EMU * h / w))


def region_box_emu(
    canvas: tuple[int, int],
    image_size: tuple[int, int],
    bbox: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """letterbox 居中：像素 bbox → slide EMU ``(left, top, width, height)``。

    把该页 image_size 等比缩放铺进画布、居中（留黑边不拉伸变形）；区域 EMU
    越画布则 clamp。image_size 非法 / bbox 零面积或逆序 → None（导出端跳过）。
    """
    cw, ch = canvas
    iw, ih = image_size
    if iw <= 0 or ih <= 0:
        return None
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return None
    scale = min(cw / iw, ch / ih)
    off_x = (cw - scale * iw) / 2
    off_y = (ch - scale * ih) / 2
    left = max(0, min(round(off_x + x1 * scale), cw))
    top = max(0, min(round(off_y + y1 * scale), ch))
    width = max(1, min(round((x2 - x1) * scale), cw - left))
    height = max(1, min(round((y2 - y1) * scale), ch - top))
    return (left, top, width, height)


# ── 构造：OCR 层 LayoutRegion → sidecar ───────────────────────


def resolve_output_image_ref(stem: str, raw_ref: str) -> str:
    """OCR 内相对引用 ``images/N.ext`` + 页 stem → 最终输出相对引用。

    镜像 ``Renderer._copy_image`` 的命名（``{stem}_{idx}.{ext}``）；``raw_ref``
    为空返回空。
    """
    if not raw_ref:
        return ""
    return f"images/{stem}_{Path(raw_ref).name}"


def layout_region_from_ocr(
    region: LayoutRegion, *, stem: str, content: str,
) -> PptLayoutRegion:
    """把 OCR 层 ``LayoutRegion`` 转成 sidecar 区域。

    ``content`` 由调用方传入（已脱敏；关精修=raw、开精修=精修后）；image/chart
    区域（``image_ref`` 非空）忽略 content、把 raw 引用映射成最终输出路径。
    """
    if region.image_ref:  # 图片区域：走裁图，颜色无意义
        return PptLayoutRegion(
            bbox=region.bbox,
            label=region.label,
            content="",
            image_ref=resolve_output_image_ref(stem, region.image_ref),
        )
    return PptLayoutRegion(
        bbox=region.bbox,
        label=region.label,
        content=content,
        image_ref="",
        fg_color=region.fg_color,  # 文字区域：透传捕获期采样色（§11）
        bg_color=region.bg_color,
    )


def build_ppt_layout(
    pages: list[tuple[str, tuple[int, int], list[PptLayoutRegion]]],
) -> PptLayout | None:
    """从每页 ``(filename, image_size, regions)`` 构造 ``PptLayout``。

    画布按**首个有效** image_size 的长宽比定（§5 决策1）。无任何区域 / 无有效
    首页尺寸 → None（非 VL 引擎或捕获失败，导出端退竖排）。
    """
    canvas: tuple[int, int] | None = None
    layout_pages: list[PptLayoutPage] = []
    total_regions = 0
    for filename, image_size, regions in pages:
        size = (int(image_size[0]), int(image_size[1]))
        if canvas is None:
            canvas = compute_canvas_emu(size)
        region_list = list(regions)
        total_regions += len(region_list)
        layout_pages.append(PptLayoutPage(
            filename=filename, image_size=size, regions=region_list,
        ))
    if canvas is None or total_regions == 0:
        return None
    return PptLayout(slide_size_emu=canvas, pages=layout_pages)


# ── 序列化 / 反序列化（fail-safe）─────────────────────────────


def to_dict(layout: PptLayout) -> dict[str, object]:
    """``PptLayout`` → JSON 可序列化 dict。"""
    return {
        "version": layout.version,
        "slide_size_emu": list(layout.slide_size_emu),
        "pages": [
            {
                "filename": page.filename,
                "image_size": list(page.image_size),
                "regions": [
                    {
                        "bbox": list(region.bbox),
                        "label": region.label,
                        "content": region.content,
                        "image_ref": region.image_ref,
                        "fg_color": (
                            list(region.fg_color)
                            if region.fg_color is not None
                            else None
                        ),
                        "bg_color": (
                            list(region.bg_color)
                            if region.bg_color is not None
                            else None
                        ),
                    }
                    for region in page.regions
                ],
            }
            for page in layout.pages
        ],
    }


def _as_rgb(raw: object) -> tuple[int, int, int] | None:
    """长度 3 的 0..255 整型序列 → RGB；缺失 / 非法 → None（不报错，向后兼容）。"""
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    try:
        vals = [int(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if any(v < 0 or v > 255 for v in vals):
        return None
    return (vals[0], vals[1], vals[2])


def _region_from_dict(raw: object) -> PptLayoutRegion | None:
    """单区域 dict → ``PptLayoutRegion``；核心字段非法返回 None。

    颜色字段（fg_color/bg_color）缺失或非法降级为 None（向后兼容旧无色 sidecar、
    不致整区失败）；核心字段（bbox/label/content/image_ref）仍严格校验。
    """
    if not isinstance(raw, dict):
        return None
    bbox = as_int_quad(raw.get("bbox"))
    label = raw.get("label")
    content = raw.get("content", "")
    image_ref = raw.get("image_ref", "")
    if (
        bbox is None
        or not isinstance(label, str)
        or not isinstance(content, str)
        or not isinstance(image_ref, str)
    ):
        return None
    return PptLayoutRegion(
        bbox=bbox,
        label=label,
        content=content,
        image_ref=image_ref,
        fg_color=_as_rgb(raw.get("fg_color")),
        bg_color=_as_rgb(raw.get("bg_color")),
    )


def from_dict(data: object) -> PptLayout | None:
    """JSON dict → ``PptLayout``；版本不符 / 任一字段非法 → None（fail-safe）。"""
    if not isinstance(data, dict) or data.get("version") != _LAYOUT_VERSION:
        return None
    canvas = as_int_pair(data.get("slide_size_emu"))
    raw_pages = data.get("pages")
    if canvas is None or not isinstance(raw_pages, list):
        return None
    pages: list[PptLayoutPage] = []
    for raw_page in raw_pages:
        if not isinstance(raw_page, dict):
            return None
        size = as_int_pair(raw_page.get("image_size"))
        filename = raw_page.get("filename")
        raw_regions = raw_page.get("regions")
        if (
            size is None
            or not isinstance(filename, str)
            or not isinstance(raw_regions, list)
        ):
            return None
        regions: list[PptLayoutRegion] = []
        for raw_region in raw_regions:
            region = _region_from_dict(raw_region)
            if region is None:
                return None
            regions.append(region)
        pages.append(PptLayoutPage(
            filename=filename, image_size=size, regions=regions,
        ))
    return PptLayout(slide_size_emu=canvas, pages=pages)


# ── 磁盘 I/O（同步；async 调用方用 asyncio.to_thread 包裹）─────


def write_ppt_layout(output_dir: Path, layout: PptLayout) -> Path:
    """把 ``PptLayout`` 写到 ``output_dir/.ppt_layout.json``，返回路径。"""
    return write_json_sidecar(output_dir / PPT_LAYOUT_FILENAME, to_dict(layout))


def load_ppt_layout(output_dir: Path) -> PptLayout | None:
    """读 ``output_dir/.ppt_layout.json`` → ``PptLayout``；缺失/损坏 → None。"""
    data = load_json_sidecar(output_dir / PPT_LAYOUT_FILENAME)
    return None if data is None else from_dict(data)
