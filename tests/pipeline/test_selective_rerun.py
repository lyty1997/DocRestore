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

"""A-2 选择性重跑：UI 噪音残留 + 重复 H2 → 段级/final re-refine 测试。

覆盖：
- 信号 1（UI 噪音）：首轮干净 → 不触发重试；有噪音 → 重试更干净则采用；
  重试异常保留首轮；重试更糟保留首轮；已是重试的 ctx 不再递归
- 信号 4（重复 H2）：首轮无重复 → 不触发；有重复 → 重试更少则采用；
  重试截断保留首轮；重试无改善保留首轮
"""

from __future__ import annotations

from typing import cast

import pytest

from docrestore.llm.base import LLMRefiner
from docrestore.models import (
    DocBoundary,
    Gap,
    RefineContext,
    RefinedResult,
)
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.quality_report import QualityReport


class _FakeRefiner:
    """测试用：按预设返回值序列回应 refine() 调用。

    通过 cast(LLMRefiner, instance) 注入到 Pipeline 的 staticmethod，
    mypy 不会基于结构性 Protocol 匹配（因为 Protocol 没标 runtime_checkable
    且这里只实现了部分方法）。所有 Pipeline.* 调用统一用 fake_as_refiner()。
    """

    def __init__(
        self, outputs: list[str | Exception],
    ) -> None:
        self._outputs = list(outputs)
        self.calls: list[RefineContext] = []

    async def refine(
        self, text: str, context: RefineContext,
    ) -> RefinedResult:
        del text
        self.calls.append(context)
        if not self._outputs:
            raise RuntimeError("no more outputs")
        nxt = self._outputs.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return RefinedResult(markdown=nxt)

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

    async def detect_doc_boundaries(
        self, merged_markdown: str,
    ) -> list[DocBoundary]:
        del merged_markdown
        return []

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        del text
        return [], []


def _as_refiner(fake: _FakeRefiner) -> LLMRefiner:
    """显式 cast _FakeRefiner 为 LLMRefiner（满足 mypy strict）。"""
    return cast("LLMRefiner", fake)


def _ctx() -> RefineContext:
    return RefineContext(
        segment_index=1, total_segments=1,
        overlap_before="", overlap_after="",
    )


@pytest.mark.asyncio
async def test_no_retry_when_output_clean() -> None:
    """首轮输出干净 → 不触发重试。"""
    refiner = _FakeRefiner(["## 标题\n干净正文\n"])
    first = await refiner.refine("raw", _ctx())

    result = await Pipeline._maybe_retry_refine_on_ui_noise(
        _as_refiner(refiner), "raw", _ctx(), first, None,
    )
    assert result.markdown == "## 标题\n干净正文\n"
    # 只 call 了第一次（手动调用），不应该再次 call
    assert len(refiner.calls) == 1


@pytest.mark.asyncio
async def test_retry_when_ui_noise_detected_and_improves() -> None:
    """首轮有 UI 噪音 → 重试更干净 → 采用重试版。"""
    refiner = _FakeRefiner([
        "正文\nPlain Text 复制代码\ncode line",  # 首轮（不会被调用）
        "正文\ncode line",                         # 重试
    ])
    first = await refiner.refine("raw", _ctx())

    result = await Pipeline._maybe_retry_refine_on_ui_noise(
        _as_refiner(refiner), "raw", _ctx(), first, None,
    )
    assert "Plain Text 复制代码" not in result.markdown
    assert len(refiner.calls) == 2
    # 第二次 call 带 retry_hint
    assert refiner.calls[1].retry_hint != ""


@pytest.mark.asyncio
async def test_retry_exception_keeps_first_result() -> None:
    """重试失败 → 保留首轮。"""
    refiner = _FakeRefiner([
        "正文\nBash 复制代码\ncode",
        RuntimeError("provider error"),
    ])
    first = await refiner.refine("raw", _ctx())

    result = await Pipeline._maybe_retry_refine_on_ui_noise(
        _as_refiner(refiner), "raw", _ctx(), first, None,
    )
    # 异常 → 保留首轮
    assert result.markdown == first.markdown


@pytest.mark.asyncio
async def test_retry_noisier_keeps_first() -> None:
    """重试反而更脏 → 保留首轮。"""
    refiner = _FakeRefiner([
        "Plain Text 复制代码\n正文",             # 首轮 1 处噪音
        "Plain Text 复制代码\nBash 复制代码",    # 重试 2 处
    ])
    first = await refiner.refine("raw", _ctx())

    result = await Pipeline._maybe_retry_refine_on_ui_noise(
        _as_refiner(refiner), "raw", _ctx(), first, None,
    )
    assert result.markdown == first.markdown


@pytest.mark.asyncio
async def test_retry_reduces_but_not_zero_still_accepted() -> None:
    """重试减少噪音但未清零 → 仍采用重试版（数量减少即视为改善）。"""
    refiner = _FakeRefiner([
        "Plain Text 复制代码\nBash 复制代码\n正文",  # 首轮 2 处
        "Plain Text 复制代码\n正文",                 # 重试 1 处
    ])
    first = await refiner.refine("raw", _ctx())
    result = await Pipeline._maybe_retry_refine_on_ui_noise(
        _as_refiner(refiner), "raw", _ctx(), first, None,
    )
    # 重试版更干净，即使还没清零也采用
    assert result.markdown.count("复制代码") == 1


@pytest.mark.asyncio
async def test_retry_quality_event_recorded() -> None:
    """重试事件应记入 quality report。"""
    refiner = _FakeRefiner([
        "Plain Text 复制代码\n正文",
        "正文",
    ])
    first = await refiner.refine("raw", _ctx())
    quality = QualityReport()
    await Pipeline._maybe_retry_refine_on_ui_noise(
        _as_refiner(refiner), "raw", _ctx(), first, quality,
    )
    codes = {i.code for i in quality.issues}
    assert "llm.seg_ui_noise_retry" in codes


@pytest.mark.asyncio
async def test_retry_marked_context_does_not_trigger_again() -> None:
    """ctx.retry_hint 已非空 → 视为已是重试结果，不再递归。"""
    # 手动构造"已是重试结果"的场景
    ctx = RefineContext(
        segment_index=1, total_segments=1,
        overlap_before="", overlap_after="",
        retry_hint="上轮有问题",
    )
    refiner = _FakeRefiner([])  # 不应被调用
    first = RefinedResult(markdown="Plain Text 复制代码\n")
    result = await Pipeline._maybe_retry_refine_on_ui_noise(
        _as_refiner(refiner), "raw", ctx, first, None,
    )
    assert result.markdown == first.markdown
    assert len(refiner.calls) == 0


# ---------------- 信号 4: final_refine 重复 H2 重做 ----------------


from unittest.mock import patch  # noqa: E402

from docrestore.models import MergedDocument  # noqa: E402
from docrestore.pipeline.quality_report import (  # noqa: E402
    find_duplicate_h2_titles,
)


def _doc(md: str) -> MergedDocument:
    return MergedDocument(markdown=md, images=[], gaps=[])


class _FakePipeline:
    """独立最小 fixture：模拟 Pipeline._maybe_retry_final_refine_on_dup_h2
    所依赖的 _do_final_refine 行为序列，避免整条 LLM 链路。
    """

    def __init__(self, outputs: list[tuple[str, bool]]) -> None:
        self._outputs = list(outputs)
        self.call_hints: list[str] = []

    async def _do_final_refine(  # type: ignore[no-untyped-def]
        self, doc, output_dir, llm, report_fn, cache, llm_cfg,
        *, retry_hint: str = "",
    ):
        self.call_hints.append(retry_hint)
        md, trunc = self._outputs.pop(0)
        return _doc(md), trunc


@pytest.mark.asyncio
async def test_signal4_no_duplicate_no_retry() -> None:
    """首轮无重复 H2 → 不触发信号 4 重试。"""
    fake = _FakePipeline([("## A\n正文", False)])
    with patch.object(
        Pipeline, "_do_final_refine",
        new=fake._do_final_refine,
    ):
        doc = _doc("## A\n正文\n## B\n正文")
        result_doc, trunc = await Pipeline._maybe_retry_final_refine_on_dup_h2(
            Pipeline.__new__(Pipeline),  # 无需 __init__，只用 staticmethod 风格
            doc, output_dir=None, llm=None, report_fn=lambda *_a, **_k: None,  # type: ignore[arg-type]
            cache=None, llm_cfg=None, quality=None,  # type: ignore[arg-type]
            initial_truncated=False,
        )
    # 首轮 doc 就没重复，直接返回；_do_final_refine 不应被调用
    assert fake.call_hints == []
    assert result_doc.markdown == "## A\n正文\n## B\n正文"
    assert trunc is False


@pytest.mark.asyncio
async def test_signal4_duplicate_retry_improves() -> None:
    """有重复 → 重试后更干净 → 采用重试版。"""
    first_md = "## 编译\nA\n## 编译\nB\n## 调试\nC\n"  # 2 处"编译"
    retry_md = "## 编译\nB\n## 调试\nC\n"               # 重试无重复
    fake = _FakePipeline([(retry_md, False)])
    with patch.object(
        Pipeline, "_do_final_refine",
        new=fake._do_final_refine,
    ):
        doc = _doc(first_md)
        result_doc, _ = await Pipeline._maybe_retry_final_refine_on_dup_h2(
            Pipeline.__new__(Pipeline), doc, None, None,  # type: ignore[arg-type]
            lambda *_a, **_k: None, None, None, None,  # type: ignore[arg-type]
            initial_truncated=False,
        )
    assert result_doc.markdown == retry_md
    # 重试 hint 应该非空 + 提到重复的标题
    assert len(fake.call_hints) == 1
    assert "编译" in fake.call_hints[0]


@pytest.mark.asyncio
async def test_signal4_retry_truncated_keeps_first() -> None:
    """重试截断 → 丢弃重试结果，保留首轮。"""
    first_md = "## A\nX\n## A\nY\n"
    fake = _FakePipeline([("随便", True)])  # truncated=True
    with patch.object(
        Pipeline, "_do_final_refine", new=fake._do_final_refine,
    ):
        doc = _doc(first_md)
        result_doc, trunc = await Pipeline._maybe_retry_final_refine_on_dup_h2(
            Pipeline.__new__(Pipeline), doc, None, None,  # type: ignore[arg-type]
            lambda *_a, **_k: None, None, None, None,  # type: ignore[arg-type]
            initial_truncated=False,
        )
    assert result_doc.markdown == first_md  # 保留首轮
    assert trunc is False  # 首轮的 truncated


@pytest.mark.asyncio
async def test_signal4_retry_no_improvement_keeps_first() -> None:
    """重试重复 H2 数量未减少 → 保留首轮。"""
    first_md = "## A\nX\n## A\nY\n"
    retry_md = "## A\nX\n## A\nY\n## A\nZ\n"  # 反而更多
    fake = _FakePipeline([(retry_md, False)])
    with patch.object(
        Pipeline, "_do_final_refine", new=fake._do_final_refine,
    ):
        doc = _doc(first_md)
        result_doc, _ = await Pipeline._maybe_retry_final_refine_on_dup_h2(
            Pipeline.__new__(Pipeline), doc, None, None,  # type: ignore[arg-type]
            lambda *_a, **_k: None, None, None, None,  # type: ignore[arg-type]
            initial_truncated=False,
        )
    assert result_doc.markdown == first_md


@pytest.mark.asyncio
async def test_signal4_initial_truncated_skips() -> None:
    """首轮已截断 → 不触发重试（保守路径已处理）。"""
    fake = _FakePipeline([])  # 不应被调用
    with patch.object(
        Pipeline, "_do_final_refine", new=fake._do_final_refine,
    ):
        doc = _doc("## A\nX\n## A\nY\n")
        result_doc, trunc = await Pipeline._maybe_retry_final_refine_on_dup_h2(
            Pipeline.__new__(Pipeline), doc, None, None,  # type: ignore[arg-type]
            lambda *_a, **_k: None, None, None, None,  # type: ignore[arg-type]
            initial_truncated=True,
        )
    assert fake.call_hints == []
    assert trunc is True


def test_find_duplicate_h2_titles_basic() -> None:
    md = (
        "## 编译\nA\n"
        "## 调试\nB\n"
        "## 编译\nC\n"
        "## 其他\nD\n"
        "## 调试\nE\n"
    )
    dups = find_duplicate_h2_titles(md)
    assert dups == ["编译", "调试"]


def test_find_duplicate_h2_titles_none() -> None:
    md = "## A\nX\n## B\nY\n"
    assert find_duplicate_h2_titles(md) == []


# ---------------- 信号 2: LLM 段截断 → 二分递归重试 ----------------


from docrestore.pipeline.config import LLMConfig  # noqa: E402


def _llm_cfg() -> LLMConfig:
    return LLMConfig(
        model="m", api_key="k",
        truncation_min_input_lines=20,
        truncation_ratio_threshold=0.3,
    )


@pytest.mark.asyncio
async def test_truncation_split_recovers() -> None:
    """长段截断 → 二分两段都成功 → 拼回完整结果。"""
    # 构造一段长输入：500 行 ≈ 5KB，足够通过默认 min_chunk_chars=800 二分
    long_text = "\n".join(f"line number {i}" for i in range(500))
    truncated_first = "line number 0\nline number 1\nline number 2"
    half_a_out = "\n".join(f"out {i}" for i in range(250))
    half_b_out = "\n".join(f"out {i + 250}" for i in range(250))

    refiner = _FakeRefiner([half_a_out, half_b_out])
    first = RefinedResult(markdown=truncated_first, truncated=True)
    result = await Pipeline._maybe_retry_on_truncation(
        _as_refiner(refiner), long_text, _ctx(), first, _llm_cfg(), None,
    )
    assert not result.truncated
    assert "out 0" in result.markdown
    assert "out 499" in result.markdown
    assert len(refiner.calls) == 2


@pytest.mark.asyncio
async def test_truncation_recursive_subdivision() -> None:
    """子段仍截断 → 继续递归二分。"""
    long_text = "\n".join(f"line number {i}" for i in range(500))
    a_truncated = "x"  # 极短，触发启发式
    b_ok = "\n".join(f"b{i}" for i in range(200))
    a1_ok = "\n".join(f"a1_{i}" for i in range(120))
    a2_ok = "\n".join(f"a2_{i}" for i in range(120))
    # 调用顺序：A → 截断 → 递归 A1, A2 → B
    refiner = _FakeRefiner([a_truncated, a1_ok, a2_ok, b_ok])
    first = RefinedResult(markdown="x", truncated=True)
    result = await Pipeline._maybe_retry_on_truncation(
        _as_refiner(refiner), long_text, _ctx(), first, _llm_cfg(), None,
    )
    assert not result.truncated
    assert "a1_0" in result.markdown
    assert "a2_119" in result.markdown
    assert "b0" in result.markdown
    assert len(refiner.calls) == 4  # 1 + 1 + 2


@pytest.mark.asyncio
async def test_truncation_max_depth_falls_back_to_raw() -> None:
    """达到 max_depth 仍截断 → 回退原文。"""
    long_text = "\n".join(f"line {i}" for i in range(800))
    # 一直返回截断输出 → 一直递归。显式标注 list 类型让 mypy 满意
    truncated_outputs: list[str | Exception] = [
        "x" for _ in range(20)  # 足够多以满足递归调用
    ]
    refiner = _FakeRefiner(truncated_outputs)
    first = RefinedResult(markdown="x", truncated=True)
    quality = QualityReport()
    result = await Pipeline._maybe_retry_on_truncation(
        _as_refiner(refiner), long_text, _ctx(), first, _llm_cfg(), quality,
        max_depth=2, min_chunk_chars=200,
    )
    # 达到 max_depth → 回退到原文
    assert result.markdown == long_text
    assert result.truncated is True
    # 必有 unrecoverable 信号被记录
    codes = {i.code for i in quality.issues}
    assert "llm.seg_truncation_unrecoverable" in codes


@pytest.mark.asyncio
async def test_truncation_too_short_to_split() -> None:
    """段太短无法二分 → 直接回退原文。"""
    short_text = "短输入只有 2 行\n第二行"  # 远 < min_chunk_chars*2
    refiner = _FakeRefiner([])  # 不应被调用
    first = RefinedResult(markdown="x", truncated=True)
    quality = QualityReport()
    result = await Pipeline._maybe_retry_on_truncation(
        _as_refiner(refiner), short_text, _ctx(), first, _llm_cfg(), quality,
        min_chunk_chars=500,
    )
    assert result.markdown == short_text
    assert result.truncated is True
    assert len(refiner.calls) == 0
    codes = {i.code for i in quality.issues}
    assert "llm.seg_truncation_unrecoverable" in codes


@pytest.mark.asyncio
async def test_truncation_subcall_exception_keeps_raw() -> None:
    """二分子段调用异常 → 整段回退原文（保守）。"""
    long_text = "\n".join(f"line {i}" for i in range(100))
    refiner = _FakeRefiner([RuntimeError("provider down")])
    first = RefinedResult(markdown="x", truncated=True)
    result = await Pipeline._maybe_retry_on_truncation(
        _as_refiner(refiner), long_text, _ctx(), first, _llm_cfg(), None,
    )
    assert result.markdown == long_text
    assert result.truncated is True


def test_split_in_half_avoids_page_marker_zone() -> None:
    """关键：page marker 在中点 + heading 在禁区外 → 切在 heading 处。

    回归用例：U-Boot 类长文档跨页重叠（marker 前后 1-3 行重复），如果在
    marker 处切，重叠区被分离到不同子段，LLM 看不到完整重复，去重失效。
    新策略应找远离 marker 的语义边界（heading 优先）。

    要求 heading 位置距 marker > avoid_chars（240）才会被选中。
    """
    pre = "前段内容填充行。\n" * 50  # 50 行 ≈ 500 chars
    middle_heading = "## 真正的章节标题\n"  # heading 起始 ≈ 500
    # 30 行 ≈ 300 chars，让 heading 离 marker > avoid_chars (240)
    mid_filler = "中段填充行内容。\n" * 30
    marker = "<!-- page: DSC04699.JPG -->\n"
    post = "后段内容填充行。\n" * 50  # 500 chars
    text = pre + middle_heading + mid_filler + marker + post

    halves = Pipeline._split_segment_in_half(text)
    assert halves is not None
    assert len(halves) == 2
    # 切点应落在 heading（heading 在 marker 禁区外）
    assert halves[1].startswith("## 真正的章节标题"), (
        f"切错位置：halves[1] 头 60 字 = {halves[1][:60]!r}"
    )
    assert not halves[1].startswith("<!-- page:")


def test_split_in_half_keeps_overlap_zone_together() -> None:
    """page marker 前后 1-2 行（典型跨页重叠区）应留在同一子段，不切开。"""
    # 构造一个简单场景：marker 前后各有"重叠行"（拍照时被两边都拍到）
    pre_filler = ("内容填充行\n" * 30)  # ~270 chars
    overlap_before = "重要的重叠行 A\n重要的重叠行 B\n"
    marker = "<!-- page: DSC04711.JPG -->\n"
    overlap_after = "重要的重叠行 A\n重要的重叠行 B\n"  # 同样的两行（拍照重叠）
    post_filler = ("后续内容填充行\n" * 30)
    text = pre_filler + overlap_before + marker + overlap_after + post_filler

    halves = Pipeline._split_segment_in_half(text)
    assert halves is not None
    # 重叠两行 + marker 整块应该完整地落在同一个 half 内（halves[0] 末尾
    # 会被 rstrip("\n")，所以匹配时不带末尾换行）
    full_overlap_block = (
        "重要的重叠行 A\n重要的重叠行 B\n"
        "<!-- page: DSC04711.JPG -->\n"
        "重要的重叠行 A\n重要的重叠行 B"
    )
    in_first = full_overlap_block in halves[0]
    in_second = full_overlap_block in halves[1]
    assert in_first or in_second, (
        "跨页重叠区被切开了！"
        f"\nhalves[0] 末 100 字: {halves[0][-100:]!r}"
        f"\nhalves[1] 头 100 字: {halves[1][:100]!r}"
    )


def test_split_in_half_falls_back_to_blank_line() -> None:
    """无 page marker → 找空行边界。"""
    text = "段落 A\n" * 20 + "\n" + "段落 B\n" * 20
    halves = Pipeline._split_segment_in_half(text)
    assert halves is not None
    # 后半应以"段落 B"开头
    assert halves[1].startswith("段落 B")


def test_split_in_half_too_short_returns_none() -> None:
    """文本不够长 → None。"""
    assert Pipeline._split_segment_in_half("x") is None
    assert Pipeline._split_segment_in_half("x" * 50) is None


def test_heuristic_truncated_small_input_skipped() -> None:
    """小输入跳过启发式（防止误判）。"""
    cfg = _llm_cfg()
    short_in = "\n".join(f"line {i}" for i in range(5))
    assert not Pipeline._heuristic_truncated(short_in, "x", cfg)


def test_heuristic_truncated_large_drop_triggers() -> None:
    cfg = _llm_cfg()
    long_in = "\n".join(f"line {i}" for i in range(100))
    short_out = "line 0"
    assert Pipeline._heuristic_truncated(long_in, short_out, cfg)


def test_heuristic_truncated_small_drop_not_triggered() -> None:
    cfg = _llm_cfg()
    long_in = "\n".join(f"line {i}" for i in range(100))
    similar_out = "\n".join(f"out {i}" for i in range(80))
    assert not Pipeline._heuristic_truncated(long_in, similar_out, cfg)
