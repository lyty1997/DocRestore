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

"""通用版面 sidecar：光标 ↔ 原图 bbox 高亮的位置真相源 ``.layout.json``（Epic E）。

文档模式收尾时落盘、前端高亮端读取后按 bbox 在原图上画矩形。本模块纯函数
（dataclass + 序列化），不依赖 OCR / PII / 导出工具，便于单测。相对 PPT 的
``.ppt_layout.json`` 减熵：去掉 EMU 画布 / 颜色 / 裁图引用，只留高亮够用的
``bbox + label + text``（raw OCR 文字，供前端模糊匹配光标块）。

反序列化采用「坏块跳过、不整页失败」的宽松策略（向后兼容、容损），与 PPT
sidecar 的严格 fail-safe 互补。设计真相源 ``docs/zh/cursor-bbox-highlight.md``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from docrestore.models import LayoutRegion

#: sidecar 文件名：dot 前缀=内部文件，不进下载 zip / asset 白名单。
DOC_LAYOUT_FILENAME = ".layout.json"
#: schema 版本：读到不匹配版本即视为无数据（fail-safe，前端不高亮）。
_LAYOUT_VERSION = 1


@dataclass(frozen=True)
class LayoutBlock:
    """版面块：原图像素 bbox + 类型 + raw OCR 文字（高亮模糊匹配用）。"""

    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) 像素（落在 image_size 内）
    label: str  # paragraph_title/text/table/image/chart...
    text: str  # raw OCR 文字（已脱敏；图片块可空）


@dataclass(frozen=True)
class LayoutPage:
    """单页版面：原图文件名 + 像素尺寸（bbox 坐标空间）+ 块列表。"""

    filename: str
    image_size: tuple[int, int]  # (width, height) 像素
    blocks: list[LayoutBlock] = field(default_factory=list)


@dataclass(frozen=True)
class DocLayout:
    """整份文档版面：各页块 bbox。"""

    pages: list[LayoutPage]
    version: int = _LAYOUT_VERSION


# ── 构造：OCR 层 LayoutRegion → sidecar ───────────────────────


def layout_block_from_region(region: LayoutRegion, *, text: str) -> LayoutBlock:
    """把 OCR 层 ``LayoutRegion`` 转成 sidecar 块。

    ``text`` 由调用方传入（已过 PII 出云闸口脱敏，与 ``document.md`` 同口径）；
    图片 / 图表块 ``content`` 本就为空 → ``text`` 空，前端模糊匹配自然不命中。
    """
    return LayoutBlock(bbox=region.bbox, label=region.label, text=text)


def build_doc_layout(
    pages: list[tuple[str, tuple[int, int], list[LayoutBlock]]],
) -> DocLayout | None:
    """从每页 ``(filename, image_size, blocks)`` 构造 ``DocLayout``。

    无任何块（非 VL 引擎 / 捕获失败）→ None，调用方不落盘、前端不高亮。
    """
    layout_pages: list[LayoutPage] = []
    total_blocks = 0
    for filename, image_size, blocks in pages:
        size = (int(image_size[0]), int(image_size[1]))
        block_list = list(blocks)
        total_blocks += len(block_list)
        layout_pages.append(LayoutPage(
            filename=filename, image_size=size, blocks=block_list,
        ))
    if total_blocks == 0:
        return None
    return DocLayout(pages=layout_pages)


# ── 序列化 ────────────────────────────────────────────────────


def to_dict(layout: DocLayout) -> dict[str, object]:
    """``DocLayout`` → JSON 可序列化 dict。"""
    return {
        "version": layout.version,
        "pages": [
            {
                "filename": page.filename,
                "image_size": list(page.image_size),
                "blocks": [
                    {
                        "bbox": list(block.bbox),
                        "label": block.label,
                        "text": block.text,
                    }
                    for block in page.blocks
                ],
            }
            for page in layout.pages
        ],
    }


# ── 反序列化（宽松：坏块跳过、不整页失败）─────────────────────


def _as_int_pair(raw: object) -> tuple[int, int] | None:
    """长度 2 的整型序列；非法返回 None。"""
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    try:
        vals = [int(v) for v in raw]
    except (TypeError, ValueError):
        return None
    return (vals[0], vals[1])


def _as_int_quad(raw: object) -> tuple[int, int, int, int] | None:
    """长度 4 的整型序列；非法返回 None。"""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        vals = [int(v) for v in raw]
    except (TypeError, ValueError):
        return None
    return (vals[0], vals[1], vals[2], vals[3])


def _block_from_dict(raw: object) -> LayoutBlock | None:
    """单块 dict → ``LayoutBlock``；字段非法返回 None（调用方跳过该块）。"""
    if not isinstance(raw, dict):
        return None
    bbox = _as_int_quad(raw.get("bbox"))
    label = raw.get("label")
    text = raw.get("text", "")
    if bbox is None or not isinstance(label, str) or not isinstance(text, str):
        return None
    return LayoutBlock(bbox=bbox, label=label, text=text)


def _page_from_dict(raw: object) -> LayoutPage | None:
    """单页 dict → ``LayoutPage``；核心字段非法返回 None（调用方跳过该页）。

    坏块逐个跳过（不致整页失败）；filename / image_size 非法才整页弃。
    """
    if not isinstance(raw, dict):
        return None
    size = _as_int_pair(raw.get("image_size"))
    filename = raw.get("filename")
    raw_blocks = raw.get("blocks")
    if size is None or not isinstance(filename, str) or not isinstance(
        raw_blocks, list,
    ):
        return None
    blocks: list[LayoutBlock] = []
    for raw_block in raw_blocks:
        block = _block_from_dict(raw_block)
        if block is not None:
            blocks.append(block)
    return LayoutPage(filename=filename, image_size=size, blocks=blocks)


def from_dict(data: object) -> DocLayout | None:
    """JSON dict → ``DocLayout``；版本不符 / 顶层非法 → None。

    坏页逐个跳过（不致整份失败）；无任何合法页 → None（前端无数据不高亮）。
    """
    if not isinstance(data, dict) or data.get("version") != _LAYOUT_VERSION:
        return None
    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list):
        return None
    pages: list[LayoutPage] = []
    for raw_page in raw_pages:
        page = _page_from_dict(raw_page)
        if page is not None:
            pages.append(page)
    if not pages:
        return None
    return DocLayout(pages=pages)


# ── 磁盘 I/O（同步；async 调用方用 asyncio.to_thread 包裹）─────


def write_doc_layout(output_dir: Path, layout: DocLayout) -> Path:
    """把 ``DocLayout`` 写到 ``output_dir/.layout.json``，返回路径。"""
    path = output_dir / DOC_LAYOUT_FILENAME
    path.write_text(
        json.dumps(to_dict(layout), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_doc_layout(output_dir: Path) -> DocLayout | None:
    """读 ``output_dir/.layout.json`` → ``DocLayout``；缺失 / 损坏 → None。"""
    path = output_dir / DOC_LAYOUT_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return from_dict(data)
