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

"""运行时自适应速率控制器。

在流式 Pipeline 中持续观测 OCR 单张耗时、LLM 单段耗时，做 EMA + 线性回归
估计出当前机器 / LLM provider 下的"目标段长 L*"，使 OCR 和 LLM 的吞吐
近似匹配，避免任一侧空闲。

核心算法见 docs/zh/backend/references/streaming-pipeline.md §5.6。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docrestore.pipeline.config import LLMConfig

logger = logging.getLogger(__name__)


class RateController:
    """运行时估计 T_ocr / overhead / k，输出目标段长 L*。

    数学模型：
    - OCR 吞吐 R_ocr = chars_per_page / T_ocr（chars/s）
    - LLM 吞吐 R_llm(L) = L / (overhead + k · L)（chars/s）
    - 令二者相等 → L* = R_ocr · overhead / (1 - R_ocr · k)
    - R_ocr · k ≥ 1 时（LLM 再大也跟不上）→ L* = MAX 摊薄 overhead

    并发约定：
    - 只在单 event loop 的协程中被调用，record_ocr / record_llm 同步更新状态，
      无 await 点，天然无需锁。
    - 多子目录并发场景下可能多协程交错调用，但 asyncio 单线程调度保证
      list.append / EMA 更新仍然原子。
    """

    #: 段长硬下界（字符数）。太小 overhead 占比过高。
    MIN_CHARS: int = 1500
    #: 段长硬上界。太大单段精度下降。
    MAX_CHARS: int = 12000
    #: 冷启动动态序列：第 i 个段切多长（索引 clamp 到最后一个）。
    COLD_START_SEQUENCE: tuple[int, ...] = (1500, 3000, 6000)
    #: 冷启动超时（秒）。到时仍未拿到 3 样本就 fallback。
    COLD_START_TIMEOUT_S: float = 60.0
    #: 单次 L* 变化幅度上限（相对上次）。防止噪声样本把段长甩飞。
    MAX_RATE_CHANGE: float = 0.3
    #: EMA 平滑系数。0.3 即"新样本权重 30%、历史权重 70%"。
    EMA_ALPHA: float = 0.3
    #: LLM 回归样本滑窗大小。
    LLM_SAMPLE_WINDOW: int = 20
    #: 样本数达到此值后进入自适应模式。
    MIN_SAMPLES_FOR_REGRESSION: int = 3

    def __init__(self, llm_config: LLMConfig | None = None) -> None:
        self._llm_config = llm_config
        self._ocr_ema: float | None = None
        self._chars_per_page_ema: float | None = None
        self._llm_samples: list[tuple[int, float]] = []
        self._overhead: float | None = None
        self._k: float | None = None
        self._last_target: int = self.COLD_START_SEQUENCE[0]
        #: 首次切到自适应时，把 _last_target 从冷启动前段 bump 到末值后置 True
        self._adaptive_initialized: bool = False
        self._queue_depth: int = 0
        self._start_monotonic: float = time.monotonic()
        self._cold_start_done: asyncio.Event = asyncio.Event()
        self._cold_start_failed: bool = False

    # ── 记录接口 ──────────────────────────────────────────

    def record_ocr(
        self, duration: float, chars: int | None = None,
    ) -> None:
        """登记一张 OCR 完成耗时。chars 非空时同步更新 chars_per_page_ema。"""
        if duration > 0:
            self._ocr_ema = self._update_ema(self._ocr_ema, duration)
        if chars is not None and chars > 0:
            self._chars_per_page_ema = self._update_ema(
                self._chars_per_page_ema, float(chars),
            )

    def record_llm(self, chars: int, duration: float) -> None:
        """登记一段 LLM refine 完成：chars 输入、duration 耗时。"""
        if chars <= 0 or duration <= 0:
            return
        self._llm_samples.append((chars, duration))
        if len(self._llm_samples) > self.LLM_SAMPLE_WINDOW:
            self._llm_samples = (
                self._llm_samples[-self.LLM_SAMPLE_WINDOW:]
            )
        self._try_regress()
        if (
            len(self._llm_samples) >= self.MIN_SAMPLES_FOR_REGRESSION
            and not self._cold_start_done.is_set()
        ):
            logger.info(
                "RateController cold start done "
                "(samples=%d, elapsed=%.1fs)",
                len(self._llm_samples),
                time.monotonic() - self._start_monotonic,
            )
            self._cold_start_done.set()

    def set_queue_depth(self, n: int) -> None:
        """观测 OCR 队列深度。目前仅供 snapshot 复盘，不参与反馈控制。"""
        self._queue_depth = n

    # ── 查询接口 ──────────────────────────────────────────

    def target_segment_chars(self) -> int:
        """返回当前推荐的段长。"""
        n_samples = len(self._llm_samples)
        if n_samples < self.MIN_SAMPLES_FOR_REGRESSION:
            idx = min(n_samples, len(self.COLD_START_SEQUENCE) - 1)
            target = self.COLD_START_SEQUENCE[idx]
            self._last_target = target
            return target

        # 首次切到自适应：如果上次 target 还停留在冷启动前段（因为冷启动期间
        # 从未被 query 过），先把 "上次" 设为冷启动末值，避免变化率限幅把
        # 首个解析解硬 clamp 回 1500。只做一次，之后信任 last_target。
        if not self._adaptive_initialized:
            if self._last_target < self.COLD_START_SEQUENCE[-1]:
                self._last_target = self.COLD_START_SEQUENCE[-1]
            self._adaptive_initialized = True

        target = self._compute_l_star()
        cap_up = int(self._last_target * (1 + self.MAX_RATE_CHANGE))
        cap_down = int(self._last_target * (1 - self.MAX_RATE_CHANGE))
        target = max(cap_down, min(cap_up, target))
        target = max(self.MIN_CHARS, min(self.MAX_CHARS, target))
        self._last_target = target
        return target

    async def wait_cold_start(self) -> None:
        """阻塞直到样本 ≥ 3 或 COLD_START_TIMEOUT_S 超时。"""
        if self._cold_start_done.is_set():
            return
        try:
            await asyncio.wait_for(
                self._cold_start_done.wait(),
                timeout=self.COLD_START_TIMEOUT_S,
            )
        except TimeoutError:
            self._cold_start_failed = True
            logger.warning(
                "RateController cold start timeout (%.1fs), "
                "samples=%d. Fallback 到保守段长，继续运行",
                self.COLD_START_TIMEOUT_S, len(self._llm_samples),
            )
            self._cold_start_done.set()

    @property
    def cold_start_done(self) -> asyncio.Event:
        """冷启动完成事件（测试用）。"""
        return self._cold_start_done

    def snapshot(self) -> dict[str, float | int | bool]:
        """返回当前状态快照，写入 profile.json 供复盘。"""
        return {
            "ocr_avg_s": self._ocr_ema or 0.0,
            "chars_per_page_avg": self._chars_per_page_ema or 0.0,
            "llm_overhead_s": self._overhead or 0.0,
            "llm_per_char_s": self._k or 0.0,
            "samples_llm": len(self._llm_samples),
            "cold_start_elapsed_s": (
                time.monotonic() - self._start_monotonic
            ),
            "cold_start_failed": self._cold_start_failed,
            "final_target_chars": self._last_target,
            "queue_depth_last": self._queue_depth,
        }

    # ── 内部实现 ──────────────────────────────────────────

    def _update_ema(
        self, prev: float | None, new_sample: float,
    ) -> float:
        """EMA 更新：prev 为空时直接用 new_sample 初始化。"""
        if prev is None:
            return new_sample
        return prev * (1 - self.EMA_ALPHA) + new_sample * self.EMA_ALPHA

    def _try_regress(self) -> None:
        """最小二乘回归 duration = overhead + k · chars。

        样本太少或 x 全相同 → 回退到简单比例估算（overhead=0）。
        """
        n = len(self._llm_samples)
        if n < 2:
            return
        xs = [float(c) for c, _ in self._llm_samples]
        ys = [d for _, d in self._llm_samples]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        dx = [x - mean_x for x in xs]
        dy = [y - mean_y for y in ys]
        denom = sum(d * d for d in dx)
        if denom <= 0:
            # 所有 x 相同 → 回归无效，退化为均值比例
            self._overhead = 0.0
            self._k = mean_y / mean_x if mean_x > 0 else 0.0
            return
        k = sum(a * b for a, b in zip(dx, dy, strict=True)) / denom
        overhead = mean_y - k * mean_x
        # k 非正说明样本噪声太大（LLM 耗时不随输入单调增），回退简化估计
        if k <= 0:
            self._overhead = 0.0
            self._k = mean_y / mean_x if mean_x > 0 else 0.0
        else:
            self._overhead = max(0.0, overhead)
            self._k = k

    def _compute_l_star(self) -> int:
        """解析解 L*：R_ocr · overhead / (1 - R_ocr · k)。

        - 缺数据或 OCR 吞吐估不出来 → 保持上次值
        - R_ocr · k ≥ 1 → LLM 吞吐上限低于 OCR，返回 MAX 摊薄 overhead
        """
        if (
            self._ocr_ema is None
            or self._chars_per_page_ema is None
            or self._overhead is None
            or self._k is None
        ):
            return self._last_target
        if self._ocr_ema <= 0:
            return self.MAX_CHARS
        r_ocr = self._chars_per_page_ema / self._ocr_ema
        if self._k <= 0:
            return self.MAX_CHARS
        denom = 1.0 - r_ocr * self._k
        if denom <= 0:
            return self.MAX_CHARS
        l_star = r_ocr * self._overhead / denom
        if l_star <= 0:
            # overhead ≈ 0 + R_ocr 很慢 → 退回最小
            return self.MIN_CHARS
        return int(l_star)
