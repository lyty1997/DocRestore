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

"""PPT 模式按页 LLM 精修 + 统一精修开关单测

验证 ``_ppt_pipeline``：
- ``enable_refine=True``：逐页调用 ``refiner.refine``（按页精修），保序组装、
  page marker 保留、RefineContext 按页递增。
- ``enable_refine=False``（统一开关关）：``_get_refiner`` 返回 None，跳过精修，
  输出原始组装结果。

合成 PageOCR + stub refiner，确定性断言，断言从构造输入派生，不写死数据集标识符。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from docrestore.llm.base import LLMRefiner
from docrestore.models import Gap, PageOCR, RefineContext, RefinedResult
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline

#: stub 精修给每页正文加的前缀，便于断言"该页确实走了精修"
_REFINE_PREFIX = "REFINED::"


class _PerPageRefiner:
    """stub：refine 给每页正文加前缀，记录每次调用的 context（断言按页 + 保序）。"""

    def __init__(self) -> None:
        self.calls: list[RefineContext] = []

    async def refine(
        self, text: str, context: RefineContext,
    ) -> RefinedResult:
        """按页精修：原样返回正文并加前缀，记录 context。"""
        self.calls.append(context)
        return RefinedResult(markdown=f"{_REFINE_PREFIX}{text}")

    async def fill_gap(
        self, gap: Gap, current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str:
        """stub：PPT 不走 gap 填充。"""
        del gap, current_page_text, next_page_text, next_page_name
        return ""

    async def final_refine(
        self, markdown: str, *,
        chunk_index: int = 1, total_chunks: int = 1,
        retry_hint: str = "",
    ) -> RefinedResult:
        """stub：PPT 按页精修不调用整篇精修。"""
        del markdown, chunk_index, total_chunks, retry_hint
        return RefinedResult(markdown="")

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """stub：不检测实体。"""
        del text
        return [], []


def _as_refiner(fake: _PerPageRefiner) -> LLMRefiner:
    """显式 cast stub 为 LLMRefiner（满足 mypy strict，结构性匹配不足）。"""
    return cast("LLMRefiner", fake)


def _make_page(output_dir: Path, stem: str, raw_text: str) -> PageOCR:
    """构造一页 PageOCR（OCR 目录建在 output_dir 下，与生产一致）。"""
    ocr_dir = output_dir / f"{stem}_OCR"
    (ocr_dir / "images").mkdir(parents=True, exist_ok=True)
    return PageOCR(
        image_path=output_dir / f"{stem}.jpg",
        image_size=(800, 600),
        raw_text=raw_text,
        output_dir=ocr_dir,
    )


def _report(
    stage: str, current: int, total: int, message: str = "",
    *, message_key: str = "", message_params: dict[str, str] | None = None,
) -> None:
    """no-op 进度回调（满足 ReportFn 协议）。"""
    del stage, current, total, message, message_key, message_params


async def _queue_of(
    pages: list[PageOCR],
) -> asyncio.Queue[PageOCR | None]:
    """构造已填充 + None 哨兵的队列，模拟 producer 出队序。"""
    queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()
    for page in pages:
        await queue.put(page)
    await queue.put(None)
    return queue


async def test_ppt_per_page_refine_applied(tmp_path: Path) -> None:
    """开关开：每页都被按页精修，保序，page marker 保留。"""
    out = tmp_path / "out"
    pages = [
        _make_page(out, "pageA", "甲页正文ALPHA"),
        _make_page(out, "pageB", "乙页正文BETA"),
    ]
    cfg = PipelineConfig(
        llm=LLMConfig(model="stub", enable_refine=True, enable_cache=False),
    )
    pipeline = Pipeline(cfg)
    fake = _PerPageRefiner()
    pipeline.set_refiner(_as_refiner(fake))

    queue = await _queue_of(pages)
    result = await pipeline._ppt_pipeline(
        queue, out, _report, llm=None, total=len(pages),
    )

    # 两页各精修一次；segment_index 按页递增、total_segments=页数
    assert len(fake.calls) == 2
    assert [c.segment_index for c in fake.calls] == [1, 2]
    assert {c.total_segments for c in fake.calls} == {2}
    md = result.markdown
    assert md.count(_REFINE_PREFIX) == 2  # 两页都走了精修
    assert "甲页正文ALPHA" in md
    assert "乙页正文BETA" in md
    assert md.index("ALPHA") < md.index("BETA")  # 保序
    assert "<!-- page: pageA.jpg -->" in md  # 内存版保留 marker


async def test_ppt_refine_disabled_skips(tmp_path: Path) -> None:
    """统一开关关（请求级 enable_refine=False）：跳过精修，输出原始组装。"""
    out = tmp_path / "out"
    pages = [_make_page(out, "pageA", "甲页正文ALPHA")]
    cfg = PipelineConfig(
        llm=LLMConfig(model="stub", enable_refine=True, enable_cache=False),
    )
    pipeline = Pipeline(cfg)
    fake = _PerPageRefiner()
    pipeline.set_refiner(_as_refiner(fake))

    queue = await _queue_of(pages)
    result = await pipeline._ppt_pipeline(
        queue, out, _report,
        llm=LLMConfig(model="stub", enable_refine=False, enable_cache=False),
        total=len(pages),
    )

    assert fake.calls == []  # _get_refiner 返回 None，refiner 从未被调用
    assert _REFINE_PREFIX not in result.markdown
    assert "甲页正文ALPHA" in result.markdown  # 原始组装结果
