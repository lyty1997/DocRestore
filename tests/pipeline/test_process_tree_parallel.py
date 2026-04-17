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

"""process_tree 多子目录并行集成测试

核心不变量：当 image_dir 含多个叶子目录时，subdir 2 的 OCR 阶段不必
等 subdir 1 的 LLM 精修结束 —— 只受 gpu_lock（OCR 串行）与
llm_semaphore（LLM 限流）约束。

观察方式：给 OCR/refine mock 打时间戳，断言 subdir 2 的 OCR 开始时间
小于 subdir 1 的 refine 结束时间。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.models import PageOCR, TaskProgress
from docrestore.pipeline.config import (
    LLMConfig,
    PIIConfig,
    PipelineConfig,
)
from docrestore.pipeline.pipeline import Pipeline


def _build_two_subdirs(root: Path) -> Path:
    """构造 root/sub1/a.jpg + root/sub2/b.jpg 两个叶子目录。"""
    for name in ("sub1", "sub2"):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / f"{name}.jpg").write_bytes(b"fake")
    return root


class TestProcessTreeParallel:
    """process_tree 的多子目录并行行为。"""

    @pytest.mark.asyncio
    async def test_subdir_ocr_overlaps_with_prior_subdir_refine(
        self, tmp_path: Path,
    ) -> None:
        """subdir2 OCR 在 subdir1 refine 结束前启动 → 跨子目录并行成立。"""
        gpu_lock = asyncio.Lock()
        ocr_events: list[tuple[str, float, float]] = []
        refine_events: list[tuple[str, float, float]] = []

        async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
            start = time.monotonic()
            await asyncio.sleep(0.03)  # 模拟 GPU 操作，持有 gpu_lock
            end = time.monotonic()
            ocr_events.append((image_path.parent.name, start, end))
            return PageOCR(
                image_path=image_path,
                image_size=(100, 100),
                raw_text=f"page {image_path.name}",
                cleaned_text=f"page {image_path.name}",
            )

        engine = MagicMock()
        engine.ocr = AsyncMock(side_effect=_ocr)
        engine.shutdown = AsyncMock(return_value=None)
        engine.is_ready = True

        async def _refine(
            raw_markdown: str, _out_dir: Path, *_a: object, **_k: object,
        ) -> SimpleNamespace:
            start = time.monotonic()
            await asyncio.sleep(0.2)  # 模拟长 LLM 调用
            end = time.monotonic()
            # 用文本里的 page marker 反查属于哪个 subdir
            tag = "sub1" if "sub1" in raw_markdown else "sub2"
            refine_events.append((tag, start, end))
            return SimpleNamespace(
                markdown=raw_markdown,
                gaps=[],
                truncated=False,
            )

        refiner = MagicMock()
        refiner.refine = AsyncMock(side_effect=_refine)
        refiner.final_refine = AsyncMock(return_value=SimpleNamespace(
            markdown="", gaps=[], truncated=False,
        ))
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        refiner.fill_gap = AsyncMock(return_value="")

        # 两张不同内容的页面，避免 dedup 把 sub2 并掉
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test/m",
                max_concurrent_requests=3,
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        image_dir = _build_two_subdirs(tmp_path / "in")
        output_dir = tmp_path / "out"

        await pipeline.process_tree(
            image_dir=image_dir,
            output_dir=output_dir,
            gpu_lock=gpu_lock,
        )

        # 基本调用次数：两个子目录都跑过 OCR + refine
        ocr_dirs = {d for d, _, _ in ocr_events}
        refine_tags = {t for t, _, _ in refine_events}
        assert ocr_dirs == {"sub1", "sub2"}, ocr_events
        assert refine_tags == {"sub1", "sub2"}, refine_events

        # 不变量：存在一对 (refine_i, ocr_j)，其中 ocr_j.start < refine_i.end
        # 即某个 subdir 的 OCR 在另一个 subdir 的 refine 未结束时就已启动
        by_dir_ocr = {d: (s, e) for d, s, e in ocr_events}
        by_dir_refine = {t: (s, e) for t, s, e in refine_events}
        sub2_ocr_start = by_dir_ocr["sub2"][0]
        sub1_refine_end = by_dir_refine["sub1"][1]
        assert sub2_ocr_start < sub1_refine_end, (
            f"sub2 OCR start={sub2_ocr_start:.3f}, "
            f"sub1 refine end={sub1_refine_end:.3f} → "
            "跨子目录并行未生效"
        )

    @pytest.mark.asyncio
    async def test_ocr_still_serialized_by_gpu_lock(
        self, tmp_path: Path,
    ) -> None:
        """跨子目录并行启用后，OCR 仍受 gpu_lock 串行保护（峰值 ≤ 1）。"""
        gpu_lock = asyncio.Lock()
        active = 0
        peak = 0

        async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return PageOCR(
                image_path=image_path,
                image_size=(100, 100),
                raw_text=f"page {image_path.name}",
                cleaned_text=f"page {image_path.name}",
            )

        engine = MagicMock()
        engine.ocr = AsyncMock(side_effect=_ocr)
        engine.shutdown = AsyncMock(return_value=None)
        engine.is_ready = True

        refiner = MagicMock()
        refiner.refine = AsyncMock(return_value=SimpleNamespace(
            markdown="", gaps=[], truncated=False,
        ))
        refiner.final_refine = AsyncMock(return_value=SimpleNamespace(
            markdown="", gaps=[], truncated=False,
        ))
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        refiner.fill_gap = AsyncMock(return_value="")

        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test/m",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        image_dir = _build_two_subdirs(tmp_path / "in")
        output_dir = tmp_path / "out"

        await pipeline.process_tree(
            image_dir=image_dir,
            output_dir=output_dir,
            gpu_lock=gpu_lock,
        )

        assert peak == 1, f"GPU 峰值并发 {peak}，应为 1（gpu_lock 串行）"

    @pytest.mark.asyncio
    async def test_progress_subtask_field_is_populated(
        self, tmp_path: Path,
    ) -> None:
        """多子目录场景下每条 progress 推送都带对应的 subtask 标识。"""
        gpu_lock = asyncio.Lock()
        collected: list[TaskProgress] = []

        def _on_progress(p: TaskProgress) -> None:
            collected.append(TaskProgress(
                stage=p.stage,
                current=p.current,
                total=p.total,
                percent=p.percent,
                message=p.message,
                subtask=p.subtask,
            ))

        async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
            await asyncio.sleep(0)
            return PageOCR(
                image_path=image_path,
                image_size=(100, 100),
                raw_text=f"page {image_path.name}",
                cleaned_text=f"page {image_path.name}",
            )

        engine = MagicMock()
        engine.ocr = AsyncMock(side_effect=_ocr)
        engine.shutdown = AsyncMock(return_value=None)
        engine.is_ready = True

        refiner = MagicMock()
        refiner.refine = AsyncMock(return_value=SimpleNamespace(
            markdown="x", gaps=[], truncated=False,
        ))
        refiner.final_refine = AsyncMock(return_value=SimpleNamespace(
            markdown="x", gaps=[], truncated=False,
        ))
        refiner.detect_doc_boundaries = AsyncMock(return_value=[])
        refiner.detect_pii_entities = AsyncMock(return_value=([], []))
        refiner.fill_gap = AsyncMock(return_value="")

        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test/m",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(engine)
        pipeline.set_refiner(refiner)

        image_dir = _build_two_subdirs(tmp_path / "in")
        output_dir = tmp_path / "out"

        await pipeline.process_tree(
            image_dir=image_dir,
            output_dir=output_dir,
            on_progress=_on_progress,
            gpu_lock=gpu_lock,
        )

        subtasks = {p.subtask for p in collected}
        # 所有推送都应带 subtask；空串不应出现
        assert "" not in subtasks, (
            f"部分 progress 未标记 subtask：{collected[:3]}"
        )
        # 两个子目录都要覆盖到
        assert subtasks == {"sub1", "sub2"}, (
            f"subtask 集合异常：{subtasks}"
        )
