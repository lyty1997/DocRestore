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

"""全局调度器：GPU 串行 + LLM API 限流

跨任务共享，确保：
- 同一时刻仅一个 OCR 任务占用 GPU（gpu_lock）
- 所有 pipeline 共享的 LLM API 并发受控（llm_semaphore）
"""

from __future__ import annotations

import asyncio


class PipelineScheduler:
    """全局调度器单例（由 app.py lifespan 创建并注入）。"""

    def __init__(self, max_concurrent_llm_requests: int = 3) -> None:
        self._gpu_lock = asyncio.Lock()
        self._llm_semaphore = asyncio.Semaphore(max_concurrent_llm_requests)

    @property
    def gpu_lock(self) -> asyncio.Lock:
        """跨任务 OCR 串行锁。"""
        return self._gpu_lock

    @property
    def llm_semaphore(self) -> asyncio.Semaphore:
        """LLM API 全局并发限流信号量（所有 pipeline 共享）。"""
        return self._llm_semaphore
