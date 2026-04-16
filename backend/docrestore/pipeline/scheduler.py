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

"""全局调度器：GPU 串行 + 下游并发限流

跨任务共享，确保：
- 同一时刻仅一个 OCR 任务占用 GPU（gpu_lock）
- 下游 pipeline（dedup → refine → render）最大并发受控（pipeline_semaphore）
"""

from __future__ import annotations

import asyncio


class PipelineScheduler:
    """全局调度器单例（由 app.py lifespan 创建并注入）。"""

    def __init__(self, max_concurrent_pipelines: int = 3) -> None:
        self._gpu_lock = asyncio.Lock()
        self._pipeline_semaphore = asyncio.Semaphore(max_concurrent_pipelines)

    @property
    def gpu_lock(self) -> asyncio.Lock:
        """跨任务 OCR 串行锁。"""
        return self._gpu_lock

    @property
    def pipeline_semaphore(self) -> asyncio.Semaphore:
        """下游 pipeline 并发限流信号量。"""
        return self._pipeline_semaphore
