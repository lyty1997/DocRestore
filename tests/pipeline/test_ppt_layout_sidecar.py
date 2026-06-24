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

"""PPT 版面 sidecar 落盘集成测试（Phase-2b · subtask 2）。

驱动 ``_ppt_pipeline`` 端到端，断言 ``.ppt_layout.json`` 落盘内容：画布按首页
长宽比、文字区域保留内容、图片区域映射最终输出引用、非 VL 不落盘、开 PII 时
文字内容过同一脱敏闸口（与 document.md 同口径）。合成 PageOCR + 派生断言。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from docrestore.models import LayoutRegion, PageOCR
from docrestore.output.ppt_layout import load_ppt_layout
from docrestore.pipeline.config import LLMConfig, PIIConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline

_SLIDE_W = 12192000


def _make_page(
    output_dir: Path,
    stem: str,
    raw_text: str,
    image_size: tuple[int, int],
    layout_regions: list[LayoutRegion],
) -> PageOCR:
    """构造一页 PageOCR（OCR 目录建在 output_dir 下，含捕获的版面区域）。"""
    ocr_dir = output_dir / f"{stem}_OCR"
    (ocr_dir / "images").mkdir(parents=True, exist_ok=True)
    return PageOCR(
        image_path=output_dir / f"{stem}.jpg",
        image_size=image_size,
        raw_text=raw_text,
        output_dir=ocr_dir,
        layout_regions=layout_regions,
    )


def _report(
    stage: str, current: int, total: int, message: str = "",
    *, message_key: str = "", message_params: dict[str, str] | None = None,
) -> None:
    """no-op 进度回调。"""
    del stage, current, total, message, message_key, message_params


async def _queue_of(
    pages: list[PageOCR],
) -> asyncio.Queue[PageOCR | None]:
    """构造已填充 + None 哨兵的队列。"""
    queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()
    for page in pages:
        await queue.put(page)
    await queue.put(None)
    return queue


def _cfg() -> PipelineConfig:
    """关精修配置（sidecar 用 raw 区域内容）。"""
    return PipelineConfig(
        llm=LLMConfig(model="stub", enable_refine=False, enable_cache=False),
    )


async def test_sidecar_written_with_regions_and_image_mapping(
    tmp_path: Path,
) -> None:
    """落 .ppt_layout.json：画布按首页长宽比、文字保内容、图片映射最终引用。"""
    out = tmp_path / "out"
    page_a = _make_page(
        out, "slideA", "# 标题甲\n\n![](images/0.jpg)", (1920, 1080),
        [
            LayoutRegion((10, 20, 100, 40), "paragraph_title", "标题甲"),
            LayoutRegion(
                (10, 60, 200, 300), "image", "", image_ref="images/0.jpg",
            ),
        ],
    )
    page_b = _make_page(
        out, "slideB", "正文乙", (1600, 1200),
        [LayoutRegion((0, 0, 100, 50), "text", "正文乙")],
    )

    pipeline = Pipeline(_cfg())
    queue = await _queue_of([page_a, page_b])
    await pipeline._ppt_pipeline(queue, out, _report, llm=None, total=2)

    layout = load_ppt_layout(out)
    assert layout is not None
    # 画布按首页（1920x1080，16:9）长宽比
    assert layout.slide_size_emu == (_SLIDE_W, 6858000)
    assert [p.filename for p in layout.pages] == ["slideA.jpg", "slideB.jpg"]
    # 首页：标题文字区域 + 图片区域映射最终输出引用 images/{stem}_N.ext
    regions_a = layout.pages[0].regions
    assert regions_a[0].label == "paragraph_title"
    assert regions_a[0].content == "标题甲"
    assert regions_a[0].image_ref == ""
    assert regions_a[1].label == "image"
    assert regions_a[1].content == ""
    assert regions_a[1].image_ref == "images/slideA_0.jpg"
    # 次页 image_size 原样带下去（bbox 坐标空间）
    assert layout.pages[1].image_size == (1600, 1200)


async def test_no_sidecar_when_no_layout_regions(tmp_path: Path) -> None:
    """非 VL 引擎（无版面区域）→ 不落 sidecar，导出端退竖排。"""
    out = tmp_path / "out"
    page = _make_page(out, "slideA", "正文", (1920, 1080), [])

    pipeline = Pipeline(_cfg())
    queue = await _queue_of([page])
    await pipeline._ppt_pipeline(queue, out, _report, llm=None, total=1)

    assert load_ppt_layout(out) is None


async def test_sidecar_text_redacted_when_pii_enabled(tmp_path: Path) -> None:
    """开 PII：文字区域内容过同一脱敏闸口，结构化 PII（手机号）被脱敏。"""
    out = tmp_path / "out"
    phone = "13800138000"
    page = _make_page(
        out, "slideA", f"联系电话{phone}", (1920, 1080),
        [LayoutRegion((0, 0, 100, 50), "text", f"联系电话{phone}")],
    )

    pipeline = Pipeline(_cfg())
    queue = await _queue_of([page])
    await pipeline._ppt_pipeline(
        queue, out, _report, llm=None, total=1,
        pii_cfg=PIIConfig(enable=True),
    )

    layout = load_ppt_layout(out)
    assert layout is not None
    content = layout.pages[0].regions[0].content
    # 手机号被结构化脱敏：原始数字串不再出现在 sidecar 内容里
    assert phone not in content
