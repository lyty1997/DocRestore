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

"""GPU 自动选择策略测试。"""

from __future__ import annotations

from docrestore.ocr.gpu_detect import GPUInfo, pick_best_gpu


def test_pick_best_gpu_prefers_newer_cuda_arch_for_ocr() -> None:
    """A2 总显存更大时，OCR/vLLM 默认仍应优先 4070 这类新架构卡。"""
    gpus = [
        GPUInfo(
            index="0",
            name="NVIDIA A2",
            memory_total_mb=15356,
            memory_free_mb=14950,
            compute_capability="8.6",
        ),
        GPUInfo(
            index="1",
            name="NVIDIA GeForce RTX 4070 SUPER",
            memory_total_mb=12282,
            memory_free_mb=11860,
            compute_capability="8.9",
        ),
    ]

    assert pick_best_gpu(gpus) == "1"


def test_pick_best_gpu_uses_free_memory_when_arch_matches() -> None:
    """同架构设备之间按空闲显存降序选择。"""
    gpus = [
        GPUInfo(
            index="0",
            name="GPU A",
            memory_total_mb=24576,
            memory_free_mb=8000,
            compute_capability="8.9",
        ),
        GPUInfo(
            index="1",
            name="GPU B",
            memory_total_mb=12288,
            memory_free_mb=11000,
            compute_capability="8.9",
        ),
    ]

    assert pick_best_gpu(gpus) == "1"


def test_pick_best_gpu_tie_breaks_by_index() -> None:
    """同规格设备使用物理索引升序保证稳定。"""
    gpus = [
        GPUInfo(index="2", name="GPU 2", memory_total_mb=8192),
        GPUInfo(index="1", name="GPU 1", memory_total_mb=8192),
    ]

    assert pick_best_gpu(gpus) == "1"
