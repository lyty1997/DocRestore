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

"""#8：消费者异常/取消时 OCR 生产者任务必须被立即取消。

旧行为：``finally: await ocr_task`` 不取消生产者，消费者提前退出后生产者仍持
``gpu_lock`` 把剩余图全 OCR 完才结束（阻塞 shutdown / 遗弃任务 + GPU 空转）。
本测试用可控的假生产者 + 中途抛错的假消费者驱动真实 ``_stream_pipeline`` 的
try/except/finally，断言：生产者收到 CancelledError、未跑完所有图、原异常上抛。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from docrestore.models import PageOCR
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline

_TOTAL_IMAGES = 8


def _make_images(image_dir: Path, n: int) -> None:
    """造 n 个占位 .jpg（假生产者不读内容，仅供 scan_images 列出）。"""
    image_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (image_dir / f"p{i}.jpg").write_bytes(b"x")


@pytest.mark.asyncio
async def test_consumer_failure_cancels_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """消费者中途抛错 → 生产者被取消、未 OCR 完所有图，且原异常向上抛。"""
    image_dir = tmp_path / "imgs"
    _make_images(image_dir, _TOTAL_IMAGES)
    out = tmp_path / "out"

    pipe = Pipeline(PipelineConfig(llm=LLMConfig(model="")))
    # 仅过 `_ocr_engine is None` 校验；生产者已被替换，不会真正使用它
    monkeypatch.setattr(pipe, "_ocr_engine", object())

    ocr_calls = 0
    producer_cancelled = asyncio.Event()

    async def fake_producer(
        images: list[Path], output_dir: Path, gpu_lock: object,
        queue: asyncio.Queue[PageOCR | None],
        *args: Any, **kwargs: Any,
    ) -> None:
        """逐图慢速入队；被取消时置事件并把哨兵补进队列（镜像真生产者 finally）。"""
        del gpu_lock, args, kwargs
        nonlocal ocr_calls
        try:
            for img in images:
                ocr_calls += 1
                await asyncio.sleep(0.01)  # 让出事件循环，使取消可中断
                await queue.put(PageOCR(
                    image_path=img, image_size=(1, 1),
                    raw_text="", cleaned_text="", output_dir=output_dir,
                ))
        except asyncio.CancelledError:
            producer_cancelled.set()
            raise
        finally:
            await queue.put(None)

    async def failing_consumer(
        page_queue: asyncio.Queue[PageOCR | None], *args: Any, **kwargs: Any,
    ) -> None:
        """取走一页让生产者先推进，再抛错模拟消费者中途失败。"""
        del args, kwargs
        await page_queue.get()
        msg = "consumer boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(pipe, "_ocr_producer", fake_producer)
    monkeypatch.setattr(pipe, "_stream_process", failing_consumer)

    with pytest.raises(RuntimeError, match="consumer boom"):
        await pipe.process_many(image_dir, out)

    assert producer_cancelled.is_set()  # 生产者被取消（而非跑完）
    assert ocr_calls < _TOTAL_IMAGES  # 未 OCR 完所有图
