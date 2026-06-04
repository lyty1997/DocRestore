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

"""全链路实体脱敏前置（人名/机构名）单测。

覆盖 `docs/zh/backend/privacy.md §9` 的设计与验收：
- 核心：`_refine_segment_with_cache` 在词表非空时把人名/机构名替换在送精修器前；
  词表为 None 时不改（早窗口 / 关脱敏 / 检测失败）。
- 助手：`_detect_entities` 在 name 开关关时返回 None（不调 LLM）、开时建词表。
- PPT 集成：`_ppt_pipeline` 开脱敏 → 最终输出不含人名（输出兜底）；关脱敏 →
  原样保留且不调实体检测。

合成 stub，断言从构造输入派生，不写死数据集标识符。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from docrestore.llm.base import LLMRefiner
from docrestore.llm.cache import LLMCache
from docrestore.models import Gap, PageOCR, RefineContext, RefinedResult
from docrestore.pipeline.config import LLMConfig, PIIConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.privacy.redactor import EntityLexicon, PIIRedactor

#: 测试用人名 / 机构名（仅本测试内构造，非数据集标识符）
_PERSON = "张三"
_ORG = "某科技公司"


class _RecordingRefiner:
    """stub：记录 refine 收到的文本 + 按需返回实体；统计实体检测调用次数。"""

    def __init__(
        self,
        entities: tuple[list[str], list[str]] = ([], []),
        *,
        raise_on_detect: bool = False,
    ) -> None:
        self.received: list[str] = []
        self.detect_calls = 0
        self._entities = entities
        self._raise_on_detect = raise_on_detect

    async def refine(
        self, text: str, context: RefineContext,
    ) -> RefinedResult:
        """原样回显（便于断言：未脱敏则人名会出现在收到的文本/输出里）。"""
        del context
        self.received.append(text)
        return RefinedResult(markdown=text)

    async def fill_gap(
        self, gap: Gap, current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str:
        del gap, current_page_text, next_page_text, next_page_name
        return ""

    async def final_refine(
        self, markdown: str, *,
        chunk_index: int = 1, total_chunks: int = 1,
        retry_hint: str = "",
    ) -> RefinedResult:
        del markdown, chunk_index, total_chunks, retry_hint
        return RefinedResult(markdown="")

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        del text
        self.detect_calls += 1
        if self._raise_on_detect:
            msg = "stub：模拟实体检测失败"
            raise RuntimeError(msg)
        return self._entities


def _as_refiner(fake: _RecordingRefiner) -> LLMRefiner:
    """显式 cast 满足 mypy strict（部分实现的结构性匹配不足）。"""
    return cast("LLMRefiner", fake)


def _make_page(output_dir: Path, stem: str, raw_text: str) -> PageOCR:
    """构造一页 PageOCR（OCR 目录建在 output_dir 下，与生产一致）。"""
    ocr_dir = output_dir / f"{stem}_OCR"
    (ocr_dir / "images").mkdir(parents=True, exist_ok=True)
    return PageOCR(
        image_path=output_dir / f"{stem}.jpg",
        image_size=(800, 600),
        raw_text=raw_text,
        cleaned_text=raw_text,
        output_dir=ocr_dir,
    )


async def _queue_of(
    pages: list[PageOCR],
) -> asyncio.Queue[PageOCR | None]:
    queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()
    for page in pages:
        await queue.put(page)
    await queue.put(None)
    return queue


def _report(
    stage: str, current: int, total: int, message: str = "",
    *, message_key: str = "", message_params: dict[str, str] | None = None,
) -> None:
    del stage, current, total, message, message_key, message_params


def _disabled_cache(tmp_path: Path) -> LLMCache:
    return LLMCache(tmp_path / ".llm_cache", enabled=False)


# ---------------- 核心：段级精修前脱敏 ----------------


@pytest.mark.asyncio
async def test_segment_redacts_entity_before_refine(tmp_path: Path) -> None:
    """词表非空：人名在送精修器前被替换（送云端前脱敏）。"""
    refiner = _RecordingRefiner()
    redactor = PIIRedactor(PIIConfig(enable=True, redact_person_name=True))
    lexicon = EntityLexicon(person_names=(_PERSON,), org_names=())

    result, used = await Pipeline._refine_segment_with_cache(
        _as_refiner(refiner), f"联系人 {_PERSON} 负责", 0, 1,
        _disabled_cache(tmp_path), LLMConfig(model="m", api_key="k"), None,
        redactor=redactor, entity_lexicon=lexicon,
    )

    assert used is True
    # 送精修器的文本已不含人名
    assert _PERSON not in refiner.received[0]
    # 精修结果（echo）也不含人名
    assert _PERSON not in result.markdown


@pytest.mark.asyncio
async def test_segment_no_lexicon_leaves_text_unchanged(
    tmp_path: Path,
) -> None:
    """词表为 None（早窗口 / 关脱敏 / 检测失败）→ 不改文本。"""
    refiner = _RecordingRefiner()
    redactor = PIIRedactor(PIIConfig(enable=True, redact_person_name=True))

    await Pipeline._refine_segment_with_cache(
        _as_refiner(refiner), f"联系人 {_PERSON} 负责", 0, 1,
        _disabled_cache(tmp_path), LLMConfig(model="m", api_key="k"), None,
        redactor=redactor, entity_lexicon=None,
    )

    assert refiner.received[0] == f"联系人 {_PERSON} 负责"


# ---------------- 助手：实体检测 ----------------


@pytest.mark.asyncio
async def test_detect_entities_off_returns_none_no_llm() -> None:
    """两 name 开关都关 → 返回 None，且不调 LLM 检测。"""
    pipe = Pipeline(PipelineConfig(llm=LLMConfig(model="m")))
    stub = _RecordingRefiner(entities=([_PERSON], [_ORG]))
    pipe.set_refiner(_as_refiner(stub))

    lex = await pipe._detect_entities(
        "正文", None,
        PIIConfig(
            enable=True, redact_person_name=False, redact_org_name=False,
        ),
    )

    assert lex is None
    assert stub.detect_calls == 0


@pytest.mark.asyncio
async def test_detect_entities_on_builds_lexicon() -> None:
    """开 name 开关 → 调 LLM 建词表。"""
    pipe = Pipeline(PipelineConfig(llm=LLMConfig(model="m")))
    stub = _RecordingRefiner(entities=([_PERSON], [_ORG]))
    pipe.set_refiner(_as_refiner(stub))

    lex = await pipe._detect_entities(
        "正文", None, PIIConfig(enable=True, redact_person_name=True),
    )

    assert lex is not None
    assert _PERSON in lex.person_names
    assert stub.detect_calls == 1


# ---------------- PPT 集成：输出兜底 + 关开关零改动 ----------------


@pytest.mark.asyncio
async def test_ppt_output_redacts_entities(tmp_path: Path) -> None:
    """PPT 开脱敏：最终输出不含人名/机构名（短 PPT 走输出兜底）。"""
    out = tmp_path / "out"
    pages = [_make_page(out, "p1", f"报告人 {_PERSON} 代表 {_ORG} 发言")]
    pipe = Pipeline(
        PipelineConfig(llm=LLMConfig(model="m", enable_cache=False)),
    )
    stub = _RecordingRefiner(entities=([_PERSON], [_ORG]))
    pipe.set_refiner(_as_refiner(stub))

    queue = await _queue_of(pages)
    result = await pipe._ppt_pipeline(
        queue, out, _report, llm=None, total=1,
        pii_cfg=PIIConfig(
            enable=True, redact_person_name=True, redact_org_name=True,
        ),
    )

    assert stub.detect_calls >= 1  # 开脱敏 → 调了实体检测
    assert _PERSON not in result.markdown
    assert _ORG not in result.markdown


@pytest.mark.asyncio
async def test_ppt_pii_off_preserves_and_skips_detect(
    tmp_path: Path,
) -> None:
    """PPT 关脱敏：原样保留人名，且不调实体检测（零改动）。"""
    out = tmp_path / "out"
    pages = [_make_page(out, "p1", f"报告人 {_PERSON} 发言")]
    pipe = Pipeline(
        PipelineConfig(llm=LLMConfig(model="m", enable_cache=False)),
    )
    stub = _RecordingRefiner(entities=([_PERSON], []))
    pipe.set_refiner(_as_refiner(stub))

    queue = await _queue_of(pages)
    result = await pipe._ppt_pipeline(
        queue, out, _report, llm=None, total=1,
        pii_cfg=PIIConfig(enable=False),
    )

    assert _PERSON in result.markdown  # 未脱敏
    assert stub.detect_calls == 0  # 关 PII → 不调实体检测


# ------- fail-closed：检测失败阻断云端（block_cloud_on_detect_failure） -------


def test_should_block_cloud_true_on_detect_failure() -> None:
    """检测失败（lexicon=None）+ 开人名脱敏 + flag=True → 阻断云端。"""
    cfg = PIIConfig(
        enable=True, redact_person_name=True,
        block_cloud_on_detect_failure=True,
    )
    assert Pipeline._should_block_cloud(None, cfg) is True


def test_should_block_cloud_false_when_flag_off() -> None:
    """flag=False → 不阻断（保持旧行为）。"""
    cfg = PIIConfig(
        enable=True, redact_person_name=True,
        block_cloud_on_detect_failure=False,
    )
    assert Pipeline._should_block_cloud(None, cfg) is False


def test_should_block_cloud_false_when_lexicon_present() -> None:
    """检测成功（非 None，含查无实体的空词表）→ 不阻断。"""
    cfg = PIIConfig(
        enable=True, redact_person_name=True,
        block_cloud_on_detect_failure=True,
    )
    empty = EntityLexicon(person_names=(), org_names=())
    assert Pipeline._should_block_cloud(empty, cfg) is False


def test_should_block_cloud_false_when_name_redaction_off() -> None:
    """未开人名/机构名脱敏 → lexicon=None 属正常（无需 LLM 检测），不算失败。"""
    cfg = PIIConfig(
        enable=True, redact_person_name=False, redact_org_name=False,
        block_cloud_on_detect_failure=True,
    )
    assert Pipeline._should_block_cloud(None, cfg) is False


@pytest.mark.asyncio
async def test_ppt_detect_failure_blocks_cloud_when_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PPT：实体检测抛错 + fail-closed → 该页不送云端精修（refine 未收到文本）。"""
    import docrestore.pipeline.pipeline as pipeline_mod

    # 阈值降到 1：单页即触发实体检测（在按页精修之前），便于断言阻断生效
    monkeypatch.setattr(pipeline_mod, "_PII_DETECT_THRESHOLD", 1)

    out = tmp_path / "out"
    pages = [_make_page(out, "p1", f"报告人 {_PERSON} 代表 {_ORG} 发言")]
    pipe = Pipeline(
        PipelineConfig(llm=LLMConfig(model="m", enable_cache=False)),
    )
    stub = _RecordingRefiner(raise_on_detect=True)
    pipe.set_refiner(_as_refiner(stub))

    queue = await _queue_of(pages)
    result = await pipe._ppt_pipeline(
        queue, out, _report, llm=None, total=1,
        pii_cfg=PIIConfig(
            enable=True, redact_person_name=True, redact_org_name=True,
            block_cloud_on_detect_failure=True,
        ),
    )

    assert stub.detect_calls >= 1  # 检测被尝试
    assert stub.received == []  # 该页未送云端精修（fail-closed 生效）
    # 名字未外发到云端；输出退原文（仍含名字，但留在本地，未传第三方）
    assert _PERSON in result.markdown


@pytest.mark.asyncio
async def test_ppt_detect_failure_still_refines_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PPT：检测抛错但 flag=False → 仍按页精修（不改旧行为，refine 收到文本）。"""
    import docrestore.pipeline.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "_PII_DETECT_THRESHOLD", 1)

    out = tmp_path / "out"
    pages = [_make_page(out, "p1", f"报告人 {_PERSON} 发言")]
    pipe = Pipeline(
        PipelineConfig(llm=LLMConfig(model="m", enable_cache=False)),
    )
    stub = _RecordingRefiner(raise_on_detect=True)
    pipe.set_refiner(_as_refiner(stub))

    queue = await _queue_of(pages)
    await pipe._ppt_pipeline(
        queue, out, _report, llm=None, total=1,
        pii_cfg=PIIConfig(
            enable=True, redact_person_name=True,
            block_cloud_on_detect_failure=False,
        ),
    )

    assert stub.detect_calls >= 1
    # flag=False：检测失败不阻断 → 该页仍送云端精修（旧行为）
    assert any(_PERSON in r for r in stub.received)
