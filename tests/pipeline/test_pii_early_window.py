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

"""早窗口防泄漏：开 PII 实体脱敏时，词表就绪前不送任何分段/页去云端精修。

注入一个本地 NER detector（返回固定人名）+ stub refiner（``refine`` 记录每次收到
的文本）。断言所有送进云端精修的文本里都不含未脱敏的人名（即词表就绪后才精修）。
S3：检测改本地 NER，故 monkeypatch ``guard.get_detector`` 注入 detector（不依赖
spaCy 是否安装）。不写死数据集标识符，断言从构造输入派生。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from docrestore.llm.base import LLMRefiner
from docrestore.llm.cache import LLMCache
from docrestore.models import Gap, PageOCR, RefineContext, RefinedResult
from docrestore.pipeline.config import (
    LLMConfig,
    PIIConfig,
    PipelineConfig,
)
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.rate_controller import RateController
from docrestore.privacy import guard as guard_mod
from docrestore.privacy.redactor import EntityLexicon
from docrestore.processing.dedup import IncrementalMerger

_NAME = "张三"  # 仅出现在 producer 之后、需 LLM 检测的人名（正则兜不住）


class _RecordingRefiner:
    """stub：refine 记录收到的文本；detect 返回固定人名词表。"""

    def __init__(self, persons: list[str]) -> None:
        self.refine_inputs: list[str] = []
        self._persons = persons

    async def refine(
        self, text: str, context: RefineContext,
    ) -> RefinedResult:
        """记录送云端精修的文本（断言不含未脱敏人名）。"""
        del context
        self.refine_inputs.append(text)
        return RefinedResult(markdown=text)

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """返回固定人名词表（模拟云端实体检测）。"""
        del text
        return list(self._persons), []

    async def final_refine(
        self, markdown: str, *,
        chunk_index: int = 1, total_chunks: int = 1, retry_hint: str = "",
    ) -> RefinedResult:
        """整篇精修也记录输入（应已脱敏）。"""
        del chunk_index, total_chunks, retry_hint
        self.refine_inputs.append(markdown)
        return RefinedResult(markdown=markdown)

    async def fill_gap(
        self, gap: Gap, current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str:
        """stub：不补缺口。"""
        del gap, current_page_text, next_page_text, next_page_name
        return ""


def _as_refiner(fake: _RecordingRefiner) -> LLMRefiner:
    """cast stub 为 LLMRefiner（结构性匹配，mypy strict 需显式）。"""
    return cast("LLMRefiner", fake)


class _FakeDetector:
    """假本地 NER detector：返回固定人名词表（替代 refiner.detect_pii_entities）。"""

    def __init__(self, persons: list[str]) -> None:
        """记录固定人名。"""
        self._persons = persons

    def detect(self, text: str) -> tuple[list[str], list[str]]:
        """返回固定人名（org 空）。"""
        del text
        return list(self._persons), []


def _patch_detector(
    monkeypatch: pytest.MonkeyPatch, persons: list[str],
) -> None:
    """注入本地 NER detector（返回固定人名），拦截 guard.get_detector。"""
    monkeypatch.setattr(
        guard_mod, "get_detector", lambda models: _FakeDetector(persons),
    )


def _make_page(output_dir: Path, stem: str, raw_text: str) -> PageOCR:
    """构造一页 PageOCR（rewrite 用 cleaned_text or raw_text → 设 raw_text 即可）。"""
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


def _pii() -> PIIConfig:
    """开 PII + 人名脱敏（机构名关，聚焦人名）。"""
    return PIIConfig(enable=True, redact_person_name=True, redact_org_name=False)


def test_entity_redaction_pending_gate() -> None:
    """门控：开 PII 且要求人名/机构名脱敏才推迟早窗口精修。"""
    assert Pipeline._entity_redaction_pending(_pii()) is True
    assert (
        Pipeline._entity_redaction_pending(
            PIIConfig(enable=True, redact_person_name=False, redact_org_name=False),
        )
        is False
    )
    assert Pipeline._entity_redaction_pending(PIIConfig(enable=False)) is False


async def test_ppt_early_window_redacts_name_before_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PPT：早窗口页的人名在送云端精修前已被词表脱敏（不外发）。"""
    _patch_detector(monkeypatch, [_NAME])
    out = tmp_path / "out"
    pages = [
        _make_page(out, "pageA", f"项目负责人{_NAME}介绍ALPHA"),
        _make_page(out, "pageB", "技术细节BETA"),
    ]
    cfg = PipelineConfig(
        llm=LLMConfig(model="stub", enable_refine=True, enable_cache=False),
        pii=_pii(),
    )
    pipeline = Pipeline(cfg)
    fake = _RecordingRefiner([_NAME])
    pipeline.set_refiner(_as_refiner(fake))

    queue = await _queue_of(pages)
    result = await pipeline._ppt_pipeline(
        queue, out, _report, llm=None, total=len(pages), pii_cfg=cfg.pii,
    )

    assert fake.refine_inputs, "应发生按页精修"
    for text in fake.refine_inputs:
        assert _NAME not in text, f"人名上云前未脱敏: {text!r}"
    assert any(
        cfg.pii.person_name_placeholder in t for t in fake.refine_inputs
    ), "词表应已应用（出现人名占位符）"
    assert _NAME not in result.markdown


async def test_doc_early_window_redacts_name_before_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文档：早窗口分段的人名在送云端精修前已被词表脱敏（不外发）。"""
    _patch_detector(monkeypatch, [_NAME])
    out = tmp_path / "out"
    # 单页 >1500 字符且含换行（供 segmenter 切点）→ 不开防护时会在词表就绪前
    # 就切段送云端，暴露开头的人名。
    filler = "技术细节说明内容。\n" * 220
    pages = [_make_page(out, "pageA", f"负责人{_NAME}。\n{filler}")]
    cfg = PipelineConfig(
        llm=LLMConfig(model="stub", enable_refine=True, enable_cache=False),
        pii=_pii(),
    )
    pipeline = Pipeline(cfg)
    fake = _RecordingRefiner([_NAME])
    pipeline.set_refiner(_as_refiner(fake))
    controller = RateController(cfg.llm)

    queue = await _queue_of(pages)
    pages_ref: list[PageOCR] = []
    result = await pipeline._stream_process(
        queue, pages_ref, out, None, None, cfg.pii, controller, _report,
    )

    assert fake.refine_inputs, "应发生分段/整篇精修"
    for text in fake.refine_inputs:
        assert _NAME not in text, f"人名上云前未脱敏: {text!r}"
    assert _NAME not in result.markdown


async def test_finalize_output_uses_request_pii_when_startup_off(
    tmp_path: Path,
) -> None:
    """#36：终结化输出实体兜底用请求级 pii_cfg，不回落 self._config.pii。

    标准部署启动级 ``self._config.pii.enable=False``；直接喂一个含未脱敏人名的
    refined_result（模拟早窗口漏脱）+ 词表 + 请求级开 PII，断言 _finalize_single_doc
    输出兜底（:2177）把人名脱掉。旧 bug 读 self._config.pii（关）则兜底失效、人名
    随最终输出落盘/外发。
    """
    out = tmp_path / "out"
    out.mkdir()
    # 启动级 PII 关闭（标准部署默认）；精修/gap-fill 全关，聚焦输出兜底脱敏
    cfg = PipelineConfig(
        llm=LLMConfig(
            model="", enable_refine=False,
            enable_final_refine=False, enable_gap_fill=False,
            enable_cache=False,
        ),
        pii=PIIConfig(enable=False),
    )
    pipeline = Pipeline(cfg)
    merger = IncrementalMerger(cfg.dedup)
    refined = [RefinedResult(markdown=f"负责人{_NAME}的报告。")]
    lexicon = EntityLexicon(person_names=(_NAME,), org_names=())
    request_pii = PIIConfig(enable=True, redact_person_name=True)
    cache = LLMCache(out / ".llm_cache", enabled=False)

    result = await pipeline._finalize_single_doc(
        merger, [], refined, [], out, None, None, _report, lexicon,
        cache, cfg.llm, None, pii_cfg=request_pii,
    )

    # 最终输出里人名已被请求级配置脱敏（占位符派生自配置，非硬编码）
    assert _NAME not in result.markdown
    assert request_pii.person_name_placeholder in result.markdown
