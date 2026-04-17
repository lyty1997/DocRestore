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

"""多任务并发 Pipeline 集成测试

覆盖两类关键场景：
1. 多任务共享 scheduler.llm_semaphore → 同一时刻进入 LLM 的调用数受上限约束；
2. Gap fill 三段锁序（llm_sem → gpu_lock → llm_sem）→ 在 re-OCR 阶段必须释放
   llm_semaphore，否则其它任务的 LLM 请求会被误阻塞。

使用 mock OCR 引擎 + 真实 CloudLLMRefiner（litellm.acompletion 被 patch），
以验证 Pipeline→_create_refiner→_call_llm 整条链路都吃到 semaphore。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docrestore.models import Gap, PageOCR
from docrestore.pipeline.config import LLMConfig, PIIConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.scheduler import PipelineScheduler


def _make_response(content: str = "refined text") -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


def _make_mock_ocr_engine(
    page_texts: dict[str, str],
    *,
    reocr_delay: float = 0.0,
    reocr_text: str = "",
    on_reocr: object = None,
) -> MagicMock:
    """OCR mock：支持 reocr_page（含可选延时 + 观察钩子）。"""
    engine = MagicMock()

    async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
        text = page_texts.get(image_path.name, f"{image_path.name} 正文")
        return PageOCR(
            image_path=image_path,
            image_size=(100, 100),
            raw_text=text,
            cleaned_text=text,
        )

    async def _reocr_page(image_path: Path) -> str:
        if on_reocr is not None:
            await on_reocr(image_path)  # type: ignore[operator]
        if reocr_delay > 0:
            await asyncio.sleep(reocr_delay)
        return reocr_text or f"reocr of {image_path.name}"

    engine.ocr = AsyncMock(side_effect=_ocr)
    engine.shutdown = AsyncMock(return_value=None)
    engine.reocr_page = AsyncMock(side_effect=_reocr_page)
    return engine


def _build_image_dir(root: Path, file_names: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in file_names:
        (root / name).write_bytes(b"fake")
    return root


class TestMultiTaskLLMSemaphore:
    """多任务并发下 llm_semaphore 的约束。"""

    @pytest.mark.asyncio
    async def test_peak_concurrent_llm_calls_bounded(
        self, tmp_path: Path,
    ) -> None:
        """两个 Pipeline 共用 semaphore(max=1) 时，acompletion 峰值并发 ≤ 1。"""
        sem = asyncio.Semaphore(1)

        active = 0
        peak = 0

        async def fake_acompletion(**_: object) -> SimpleNamespace:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            # 拉长一点，拉开两个 pipeline 的时间窗便于观察
            await asyncio.sleep(0.02)
            active -= 1
            return _make_response("ok")

        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test/m",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )

        async def run_task(tag: str) -> None:
            pipeline = Pipeline(cfg)
            pipeline.set_llm_semaphore(sem)
            pipeline.set_ocr_engine(
                _make_mock_ocr_engine({f"{tag}.jpg": f"text {tag}"}),
            )
            # _refine_segments 走 self._refiner；
            # set_refiner 可接受 Pipeline 自产的真 refiner
            pipeline.set_refiner(pipeline._create_refiner(cfg.llm))
            img_dir = _build_image_dir(tmp_path / tag, [f"{tag}.jpg"])
            out_dir = tmp_path / "out" / tag
            await pipeline.process_many(img_dir, out_dir)

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            await asyncio.gather(run_task("a"), run_task("b"))

        assert peak == 1, f"观察到峰值并发 {peak}，应为 1"
        assert active == 0

    @pytest.mark.asyncio
    async def test_scheduler_llm_semaphore_shared_across_pipelines(
        self, tmp_path: Path,
    ) -> None:
        """从 PipelineScheduler 获取的 llm_semaphore 在多 Pipeline 间共享。"""
        scheduler = PipelineScheduler(max_concurrent_llm_requests=2)

        active = 0
        peak = 0

        async def fake_acompletion(**_: object) -> SimpleNamespace:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return _make_response("ok")

        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test/m",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )

        async def run_task(tag: str) -> None:
            pipeline = Pipeline(cfg)
            pipeline.set_llm_semaphore(scheduler.llm_semaphore)
            pipeline.set_ocr_engine(
                _make_mock_ocr_engine({f"{tag}.jpg": f"text {tag}"}),
            )
            pipeline.set_refiner(pipeline._create_refiner(cfg.llm))
            img_dir = _build_image_dir(tmp_path / tag, [f"{tag}.jpg"])
            out_dir = tmp_path / "out" / tag
            await pipeline.process_many(img_dir, out_dir)

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            side_effect=fake_acompletion,
        ):
            # 3 个任务 + semaphore=2 → peak 应为 2
            await asyncio.gather(
                run_task("a"), run_task("b"), run_task("c"),
            )

        assert peak <= 2
        assert active == 0


class TestGapFillLockSequence:
    """验证 gap fill 三段锁序：llm_sem → gpu_lock（re-OCR）→ llm_sem。

    关键不变量：re-OCR 阶段必须已释放 llm_semaphore，否则 sem(max=1) 场景下
    其它任务的 LLM 调用将被误阻塞。本测试直接观察该释放行为。
    """

    @pytest.mark.asyncio
    async def test_reocr_releases_llm_semaphore(
        self, tmp_path: Path,
    ) -> None:
        """进入 reocr_page 时，llm_semaphore 应处于"可被非阻塞获取"状态。"""
        sem = asyncio.Semaphore(1)

        reocr_entered = asyncio.Event()
        observer_acquired_sem = asyncio.Event()

        async def on_reocr(_image: Path) -> None:
            # 标记进入 re-OCR 阶段，让外部 observer 立刻尝试抢 sem
            reocr_entered.set()
            # 给 observer 一点时间尝试
            await asyncio.sleep(0.02)

        async def observer() -> None:
            await reocr_entered.wait()
            # 如果 pipeline 仍持有 sem，这里会超时
            await asyncio.wait_for(sem.acquire(), timeout=0.5)
            observer_acquired_sem.set()
            sem.release()

        # 构造单页 gap：refine 返回一个 Gap，gap fill 阶段会触发 reocr
        gap = Gap(
            after_image="p.jpg",
            context_before="ctx before",
            context_after="ctx after",
        )
        call_counter = {"n": 0}

        async def fake_acompletion(**_: object) -> SimpleNamespace:
            call_counter["n"] += 1
            # 第 1 次调用：refine（返回带 GAP 标记的文本）
            # 第 2 次调用：fill_gap（返回补充内容）
            # 第 3 次调用：final_refine（如启用）
            if call_counter["n"] == 1:
                # 通过 prompts.parse_gaps 可识别的格式注入一个 gap
                return _make_response(
                    "正文内容\n"
                    "<!-- gap after=p.jpg before=ctx before after=ctx after -->",
                )
            return _make_response("filled content")

        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test/m",
                enable_gap_fill=True,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_llm_semaphore(sem)
        pipeline.set_ocr_engine(
            _make_mock_ocr_engine(
                {"p.jpg": "原始正文"},
                on_reocr=on_reocr,
                reocr_text="re-OCRed page content",
            ),
        )

        # 直接注入一个会汇报 gap 的 mock refiner（绕过 parse_gaps 依赖）
        refiner = MagicMock()
        refiner.refine = AsyncMock(return_value=MagicMock(
            markdown="正文内容", gaps=[gap], truncated=False,
        ))
        refiner.final_refine = AsyncMock(return_value=MagicMock(
            markdown="正文内容", gaps=[], truncated=False,
        ))
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))

        # fill_gap 必须走 semaphore 以验证"先释放再重新获取"：
        # 这里真实地用 sem 包一层，模拟真实 refiner 的行为
        async def _rate_limited_fill_gap(*_args: object, **_kwargs: object) -> str:
            async with sem:
                await asyncio.sleep(0.01)
                return "filled content"

        refiner.fill_gap = AsyncMock(side_effect=_rate_limited_fill_gap)
        pipeline.set_refiner(refiner)

        img_dir = _build_image_dir(tmp_path / "t", ["p.jpg"])
        out_dir = tmp_path / "out" / "t"

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=fake_acompletion,
        ):
            await asyncio.gather(
                pipeline.process_many(img_dir, out_dir),
                observer(),
            )

        assert observer_acquired_sem.is_set(), (
            "re-OCR 阶段未释放 llm_semaphore，observer 超时未拿到 sem"
        )
