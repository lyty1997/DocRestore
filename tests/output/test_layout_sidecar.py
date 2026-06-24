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

"""通用版面 sidecar ``layout_sidecar`` 纯模块测试（Epic E · E1）。

覆盖：``build_doc_layout`` 空块返回 None / round-trip 保真 / 反序列化「坏块跳过、
坏页跳过、不整份失败」的宽松 fail-safe / 版本不符与损坏文件 → None / 磁盘 I/O。
"""

from __future__ import annotations

import json
from pathlib import Path

from docrestore.models import LayoutRegion
from docrestore.output.layout_sidecar import (
    DOC_LAYOUT_FILENAME,
    DocLayout,
    LayoutBlock,
    LayoutPage,
    build_doc_layout,
    from_dict,
    layout_block_from_region,
    load_doc_layout,
    to_dict,
    write_doc_layout,
)


def _sample_layout() -> DocLayout:
    """两页、各带块的样例版面。"""
    return DocLayout(pages=[
        LayoutPage(
            filename="IMG_0001.jpg",
            image_size=(3024, 4032),
            blocks=[
                LayoutBlock((120, 88, 2900, 240), "paragraph_title", "第一章"),
                LayoutBlock((120, 260, 2900, 980), "text", "正文一"),
            ],
        ),
        LayoutPage(
            filename="IMG_0002.jpg",
            image_size=(3024, 4032),
            blocks=[LayoutBlock((100, 100, 500, 200), "text", "正文二")],
        ),
    ])


def test_layout_block_from_region_carries_redacted_text() -> None:
    """OCR 区域 → sidecar 块：bbox/label 透传、text 用调用方传入（已脱敏）值。"""
    region = LayoutRegion((1, 2, 3, 4), "text", "原始内容")
    block = layout_block_from_region(region, text="脱敏后")
    assert block.bbox == (1, 2, 3, 4)
    assert block.label == "text"
    assert block.text == "脱敏后"


def test_build_doc_layout_none_when_no_blocks() -> None:
    """所有页都无块（非 VL 引擎）→ None，调用方不落盘。"""
    pages: list[tuple[str, tuple[int, int], list[LayoutBlock]]] = [
        ("a.jpg", (100, 200), []),
        ("b.jpg", (100, 200), []),
    ]
    assert build_doc_layout(pages) is None


def test_build_doc_layout_keeps_pages_when_any_block() -> None:
    """有任意块 → 构造 DocLayout，保留所有页（含无块页，image_size 归一为 int）。"""
    pages: list[tuple[str, tuple[int, int], list[LayoutBlock]]] = [
        ("a.jpg", (100, 200), [LayoutBlock((0, 0, 10, 10), "text", "x")]),
        ("b.jpg", (100, 200), []),
    ]
    layout = build_doc_layout(pages)
    assert layout is not None
    assert [p.filename for p in layout.pages] == ["a.jpg", "b.jpg"]
    assert layout.pages[1].blocks == []


def test_round_trip_preserves_all_fields() -> None:
    """to_dict → from_dict 保真：filename/image_size/每块 bbox/label/text 不丢。"""
    layout = _sample_layout()
    restored = from_dict(to_dict(layout))
    assert restored is not None
    assert restored == layout


def test_from_dict_version_mismatch_returns_none() -> None:
    """版本不符 → None（fail-safe，前端不高亮）。"""
    data = to_dict(_sample_layout())
    data["version"] = 999
    assert from_dict(data) is None


def test_from_dict_skips_bad_block_not_whole_page() -> None:
    """坏块（bbox 长度错）逐个跳过，不致整页失败：合法块仍保留。"""
    data = {
        "version": 1,
        "pages": [{
            "filename": "a.jpg",
            "image_size": [100, 200],
            "blocks": [
                {"bbox": [0, 0, 10], "label": "text", "text": "坏块缺一维"},
                {"bbox": [0, 0, 10, 10], "label": "text", "text": "好块"},
            ],
        }],
    }
    layout = from_dict(data)
    assert layout is not None
    assert len(layout.pages[0].blocks) == 1
    assert layout.pages[0].blocks[0].text == "好块"


def test_from_dict_skips_bad_page_not_whole_doc() -> None:
    """坏页（缺 image_size）整页跳过，但其余合法页保留。"""
    data = {
        "version": 1,
        "pages": [
            {"filename": "bad.jpg", "blocks": []},  # 缺 image_size
            {
                "filename": "good.jpg",
                "image_size": [100, 200],
                "blocks": [{"bbox": [0, 0, 5, 5], "label": "text", "text": "y"}],
            },
        ],
    }
    layout = from_dict(data)
    assert layout is not None
    assert [p.filename for p in layout.pages] == ["good.jpg"]


def test_from_dict_none_when_no_valid_page() -> None:
    """无任何合法页 → None。"""
    data = {"version": 1, "pages": [{"filename": "x.jpg"}]}  # 缺 image_size
    assert from_dict(data) is None


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    """write_doc_layout → load_doc_layout 端到端保真，文件名为 .layout.json。"""
    layout = _sample_layout()
    path = write_doc_layout(tmp_path, layout)
    assert path.name == DOC_LAYOUT_FILENAME
    assert path.exists()
    assert load_doc_layout(tmp_path) == layout


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """无 sidecar 文件 → None。"""
    assert load_doc_layout(tmp_path) is None


def test_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    """损坏 JSON → None（不抛异常）。"""
    (tmp_path / DOC_LAYOUT_FILENAME).write_text("{not json", encoding="utf-8")
    assert load_doc_layout(tmp_path) is None


def test_written_json_is_utf8_readable(tmp_path: Path) -> None:
    """落盘 JSON 以 UTF-8 存中文（ensure_ascii=False），可直接解析。"""
    write_doc_layout(tmp_path, _sample_layout())
    raw = json.loads(
        (tmp_path / DOC_LAYOUT_FILENAME).read_text(encoding="utf-8"),
    )
    assert raw["pages"][0]["blocks"][0]["text"] == "第一章"
