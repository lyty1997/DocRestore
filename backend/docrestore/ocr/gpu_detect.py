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

"""GPU 设备探测 — 用户未显式指定 gpu_id 时按显存降序自动挑选。

优先 pynvml（随 torch 带上，无需额外依赖）；pynvml 不可用时回退
`nvidia-smi --query-gpu=...`。两条路径都失败则返回空列表，由调用方决定是
否退回 "0" 或报错。

为避免每次 API 请求都打开 nvml / fork nvidia-smi，结果缓存在模块内；
`refresh()` 可显式失效缓存（热插拔极罕见，默认不自动刷新）。
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import threading
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class GPUInfo(BaseModel):
    """单张 GPU 的静态元信息 + 当前可用显存快照。"""

    # CUDA_VISIBLE_DEVICES 里的物理索引；保持 str 类型与 config.gpu_id 一致
    index: str
    name: str  # 型号，例如 "NVIDIA GeForce RTX 4070 SUPER"
    memory_total_mb: int  # 总显存，MiB
    # 查询瞬刻可用显存，MiB；pynvml / nvidia-smi 回退路径都会填
    memory_free_mb: int | None = None
    compute_capability: str | None = None  # 形如 "8.9"；拿不到留空


_cache_lock = threading.Lock()
_cached: list[GPUInfo] | None = None


def list_gpus(*, use_cache: bool = True) -> list[GPUInfo]:
    """枚举系统内所有可见的 NVIDIA GPU，按物理索引升序返回。

    use_cache=True 时命中进程级缓存，避免每次 API 请求都触发 NVML 初始化。
    """
    global _cached
    if use_cache and _cached is not None:
        return _cached

    with _cache_lock:
        if use_cache and _cached is not None:
            return _cached
        gpus = _probe_pynvml()
        if gpus is None:
            gpus = _probe_nvidia_smi()
        if gpus is None:
            gpus = []
        _cached = gpus
        return gpus


def refresh() -> list[GPUInfo]:
    """强制清空缓存后重新探测，返回新结果。"""
    global _cached
    with _cache_lock:
        _cached = None
    return list_gpus(use_cache=False)


def pick_best_gpu(gpus: Sequence[GPUInfo] | None = None) -> str | None:
    """按显存降序选第一张，空列表返回 None。

    排序关键字：`memory_total_mb DESC, memory_free_mb DESC (None 视为 -1), index ASC`。
    tie-breaker 用物理索引升序，保证同构多卡下行为稳定。
    """
    candidates = list(gpus) if gpus is not None else list_gpus()
    if not candidates:
        return None

    def _sort_key(g: GPUInfo) -> tuple[int, int, int]:
        free = g.memory_free_mb if g.memory_free_mb is not None else -1
        try:
            idx_num = int(g.index)
        except ValueError:
            idx_num = 1 << 30  # 非数字索引排最后
        return (-g.memory_total_mb, -free, idx_num)

    return sorted(candidates, key=_sort_key)[0].index


# ── pynvml 路径 ──────────────────────────────────────────────────────


def _probe_pynvml() -> list[GPUInfo] | None:
    """通过 pynvml 读取；导入失败或驱动异常返回 None 触发回退。"""
    try:
        import pynvml  # type: ignore[import-not-found,import-untyped,unused-ignore]
    except ImportError:
        return None

    try:
        pynvml.nvmlInit()
    except Exception as exc:  # noqa: BLE001 — NVMLError 分支多，统一降级
        logger.debug("nvmlInit 失败，回退 nvidia-smi: %s", exc)
        return None

    try:
        count = pynvml.nvmlDeviceGetCount()
        gpus: list[GPUInfo] = []
        for i in range(count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = (
                    raw_name.decode("utf-8", errors="replace")
                    if isinstance(raw_name, bytes)
                    else str(raw_name)
                )
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                try:
                    major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(
                        handle,
                    )
                    cc = f"{major}.{minor}"
                except Exception:  # noqa: BLE001
                    cc = None
                gpus.append(
                    GPUInfo(
                        index=str(i),
                        name=name,
                        memory_total_mb=int(mem.total // (1024 * 1024)),
                        memory_free_mb=int(mem.free // (1024 * 1024)),
                        compute_capability=cc,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("读取 GPU %d 信息失败: %s", i, exc)
        return gpus
    finally:
        with contextlib.suppress(Exception):
            pynvml.nvmlShutdown()


# ── nvidia-smi 回退路径 ──────────────────────────────────────────────


def _probe_nvidia_smi() -> list[GPUInfo] | None:
    """调 `nvidia-smi --query-gpu=...` 拿 CSV，失败返回 None。"""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None

    argv = [
        nvidia_smi,
        "--query-gpu=index,name,memory.total,memory.free,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(  # noqa: S603 — argv 来自 shutil.which
            argv,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("nvidia-smi 调用失败: %s", exc)
        return None

    if proc.returncode != 0:
        logger.debug(
            "nvidia-smi 非零退出 %d: %s",
            proc.returncode, proc.stderr.strip(),
        )
        return None

    gpus: list[GPUInfo] = []
    for line in proc.stdout.splitlines():
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 3:
            continue
        index, name, mem_total = fields[0], fields[1], fields[2]
        mem_free = fields[3] if len(fields) > 3 else ""
        cc = fields[4] if len(fields) > 4 else ""
        try:
            total = int(float(mem_total))
        except ValueError:
            continue
        try:
            free: int | None = int(float(mem_free)) if mem_free else None
        except ValueError:
            free = None
        gpus.append(
            GPUInfo(
                index=index,
                name=name,
                memory_total_mb=total,
                memory_free_mb=free,
                compute_capability=cc or None,
            ),
        )
    return gpus
