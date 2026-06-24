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

"""文档模式版面 sidecar ``.layout.json`` 落盘测试（Epic E · E1）。

直接驱动 ``Pipeline._write_doc_layout_sidecar``（``_finalize_single_doc`` 收尾时
调用的同一方法）：断言块 bbox/label/text 保真、重叠两页块互不串位、非 VL 不落盘、
开 PII 时文字过同一脱敏闸口（与 document.md 同口径）。合成 PageOCR + 派生断言。
"""

from __future__ import annotations

from pathlib import Path

from docrestore.models import LayoutRegion, PageOCR
from docrestore.output.layout_sidecar import DOC_LAYOUT_FILENAME, load_doc_layout
from docrestore.pipeline.config import LLMConfig, PIIConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline


def _cfg() -> PipelineConfig:
    """关精修配置（sidecar 用 raw 区域内容）。"""
    return PipelineConfig(
        llm=LLMConfig(model="stub", enable_refine=False, enable_cache=False),
    )


def _make_page(
    output_dir: Path,
    stem: str,
    image_size: tuple[int, int],
    layout_regions: list[LayoutRegion],
) -> PageOCR:
    """构造一页 PageOCR（含捕获的版面区域；文档模式 image_path 即原图）。"""
    ocr_dir = output_dir / f"{stem}_OCR"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    return PageOCR(
        image_path=output_dir / f"{stem}.jpg",
        image_size=image_size,
        raw_text="",
        output_dir=ocr_dir,
        layout_regions=layout_regions,
    )


async def test_sidecar_written_with_block_bbox_and_text(tmp_path: Path) -> None:
    """落 .layout.json：每块 bbox/label/text 保真，按页 filename + image_size。"""
    out = tmp_path / "out"
    out.mkdir()
    page = _make_page(
        out, "IMG_0001", (3024, 4032),
        [
            LayoutRegion((120, 88, 2900, 240), "paragraph_title", "第一章 绪论"),
            LayoutRegion((120, 260, 2900, 980), "text", "本文研究……"),
        ],
    )

    pipeline = Pipeline(_cfg())
    await pipeline._write_doc_layout_sidecar(
        [page], out, PIIConfig(enable=False), None,
    )

    layout = load_doc_layout(out)
    assert layout is not None
    assert [p.filename for p in layout.pages] == ["IMG_0001.jpg"]
    assert layout.pages[0].image_size == (3024, 4032)
    blocks = layout.pages[0].blocks
    assert [b.bbox for b in blocks] == [
        (120, 88, 2900, 240), (120, 260, 2900, 980),
    ]
    assert [b.label for b in blocks] == ["paragraph_title", "text"]
    assert [b.text for b in blocks] == ["第一章 绪论", "本文研究……"]


async def test_overlapping_pages_keep_blocks_independent(
    tmp_path: Path,
) -> None:
    """重叠两页（同内容拍两次）→ 各页 sidecar 块互不串位（无需裁剪映射）。"""
    out = tmp_path / "out"
    out.mkdir()
    # 两页含一处重复块（重叠区）+ 各自独有块
    page_a = _make_page(
        out, "IMG_0001", (3024, 4032),
        [
            LayoutRegion((0, 0, 100, 50), "text", "重复段落"),
            LayoutRegion((0, 60, 100, 120), "text", "甲独有"),
        ],
    )
    page_b = _make_page(
        out, "IMG_0002", (3024, 4032),
        [
            LayoutRegion((0, 0, 100, 50), "text", "重复段落"),
            LayoutRegion((0, 60, 100, 120), "text", "乙独有"),
        ],
    )

    pipeline = Pipeline(_cfg())
    await pipeline._write_doc_layout_sidecar(
        [page_a, page_b], out, PIIConfig(enable=False), None,
    )

    layout = load_doc_layout(out)
    assert layout is not None
    assert [p.filename for p in layout.pages] == [
        "IMG_0001.jpg", "IMG_0002.jpg",
    ]
    # 各页保全自己的全部块，独有块只属本页
    assert [b.text for b in layout.pages[0].blocks] == ["重复段落", "甲独有"]
    assert [b.text for b in layout.pages[1].blocks] == ["重复段落", "乙独有"]


async def test_no_sidecar_when_no_layout_regions(tmp_path: Path) -> None:
    """非 VL 引擎（无版面区域）→ 不落 sidecar，前端无数据不高亮。"""
    out = tmp_path / "out"
    out.mkdir()
    page = _make_page(out, "IMG_0001", (3024, 4032), [])

    pipeline = Pipeline(_cfg())
    await pipeline._write_doc_layout_sidecar(
        [page], out, PIIConfig(enable=False), None,
    )

    assert load_doc_layout(out) is None
    assert not (out / DOC_LAYOUT_FILENAME).exists()


async def test_sidecar_text_redacted_when_pii_enabled(tmp_path: Path) -> None:
    """开 PII：块文字过同一脱敏闸口，结构化 PII（手机号）被脱敏。"""
    out = tmp_path / "out"
    out.mkdir()
    phone = "13800138000"
    page = _make_page(
        out, "IMG_0001", (3024, 4032),
        [LayoutRegion((0, 0, 100, 50), "text", f"联系电话{phone}")],
    )

    pipeline = Pipeline(_cfg())
    await pipeline._write_doc_layout_sidecar(
        [page], out, PIIConfig(enable=True), None,
    )

    layout = load_doc_layout(out)
    assert layout is not None
    text = layout.pages[0].blocks[0].text
    assert phone not in text
