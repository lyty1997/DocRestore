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

在流式 Pipeline 中持续观测 OCR 单张耗时、LLM 单段耗时，估计当前机器 /
LLM provider 下的"目标段长 L*"。

两个目标函数叠加使用：
- OCR 瓶颈（`R_ocr · k < 1`）：用解析解 L* = R_ocr · overhead / (1 - R_ocr · k)
  做吞吐匹配，让 LLM 刚好跟上 OCR，避免段过长拖慢整体
- LLM 瓶颈（`R_ocr · k ≥ 1`）：不再盲目 L* = MAX，改走
  `L* = argmax R_llm(L) s.t. L < L_knee`。按 L 轴分桶维护吞吐 EMA，
  爬山探索寻找非线性拐点，防 provider 超线性变慢导致负优化

详见 docs/zh/backend/references/streaming-pipeline.md §5.6。
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docrestore.pipeline.config import LLMConfig

logger = logging.getLogger(__name__)


class RateController:
    """运行时估计 T_ocr / overhead / k，输出目标段长 L*。

    数学模型
    ========
    - OCR 吞吐 `R_ocr = chars_per_page / T_ocr`（chars/s）
    - LLM 吞吐 `R_llm(L) = L / (overhead + k · L)`（chars/s，线性假设）
    - OCR 瓶颈：令 R_ocr = R_llm → L* = R_ocr · overhead / (1 - R_ocr · k)
    - LLM 瓶颈：最大化 R_llm(L) 但受非线性拐点 L_knee 约束

    非线性拐点学习（LLM 瓶颈分支）
    =============================
    把 L 轴分成若干桶 BUCKET_EDGES，每桶维护吞吐 r_ema 和耗时中位数 med_t。
    爬山向上探索：从 best_idx 出发，尝试 best_idx+1；若新桶 r_ema 明显
    低于 best（低于 KNEE_DROP_RATIO），判定越过拐点，锁定 best_idx。
    REEXPLORE_EVERY 段后重新试探下一个桶，防止 provider 漂移。

    鲁棒性护栏
    =========
    - 异常过滤：duration > 桶内 med_t × OUTLIER_MULTIPLIER 时不更新 r_ema
      （保留在原始 samples 供线性回归看，避免异常值污染拐点判定）
    - ±30% 变化率限幅（MAX_RATE_CHANGE）
    - L ∈ [MIN_CHARS, MAX_CHARS] 硬 clamp
    - 桶样本 < MIN_SAMPLES_PER_BUCKET 时不参与 argmax

    并发约定
    =======
    - 只在单 event loop 的协程中被调用，record_ocr / record_llm 同步更新
      状态，无 await 点，天然无需锁
    - 多子目录并发场景下多协程交错调用，但 asyncio 单线程调度保证
      list.append / dict 更新 / 整型自增仍然原子
    """

    #: 段长硬下界（字符数）。太小 overhead 占比过高。
    MIN_CHARS: int = 1500
    #: 段长硬上界。超出此值没有可用样本证据，统一 clamp。
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
    #: L 轴分桶边界（含首末）。桶数 = len(BUCKET_EDGES) - 1。
    #: 边界选在常见非线性 provider 的候选拐点附近（6k / 8k / 10k）。
    BUCKET_EDGES: tuple[int, ...] = (
        1500, 3000, 4500, 6000, 8000, 10000, 12000,
    )
    #: 桶"覆盖采样"所需样本数：Phase A 覆盖扫描 + outlier 基线用这个阈值。
    #: 旧名 MIN_SAMPLES_PER_BUCKET，同时兼作 argmax qualified 门槛 —— 后者
    #: 现单独用 MIN_SAMPLES_FOR_ARGMAX=1 放宽，让单样本桶也能被选为 best，
    #: 防止"探索请求 target=N 但 segmenter buffer 长期凑不齐 → 那个桶只有
    #: 1 样本 → 永远不 qualified → 永远不被选 → 桶 0 霸权"恶性循环。
    MIN_SAMPLES_PER_BUCKET: int = 2
    #: argmax 挑选 best_idx 时接受的最小桶样本数。放宽到 1 让"探索过一次
    #: 就有候选资格"，观测到的最高吞吐桶立刻参与竞争，不必等第二个样本。
    MIN_SAMPLES_FOR_ARGMAX: int = 1
    #: 探索意图超时（秒）：pending_exploration 在此时长内若仍未采到样本
    #: （buffer 长期凑不够大 target），放弃本次探索避免死锁。
    EXPLORATION_TIMEOUT_S: float = 30.0
    #: 更大桶吞吐相对 best 的下跌阈值：低于 best × KNEE_DROP_RATIO
    #: 即判定越过拐点，停止上探。0.9 = 允许 10% 的抖动容忍。
    KNEE_DROP_RATIO: float = 0.9
    #: 异常样本倍数：duration > 桶内 med_t × 该值 → 不更新 r_ema。
    OUTLIER_MULTIPLIER: float = 3.0
    #: 稳态每采样多少段后强制重探索一次相邻更大桶（检测拐点漂移）。
    REEXPLORE_EVERY: int = 30

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
        # 桶统计（len = 桶数 = BUCKET_EDGES 间隔数）
        n_buckets = len(self.BUCKET_EDGES) - 1
        self._bucket_r_ema: list[float | None] = [None] * n_buckets
        self._bucket_med_t: list[float | None] = [None] * n_buckets
        self._bucket_count: list[int] = [0] * n_buckets
        self._outlier_count: int = 0  # 累计被过滤的异常样本数，供 snapshot
        self._samples_since_reexplore: int = 0
        #: 上次 argmax 结果索引；用于爬山探索和重探索
        self._best_bucket_idx: int = 0
        #: 正在主动探索的桶索引（非 None 表示处于"上探索或重探索"中）。
        #: 每次 target_segment_chars 调用都会重算，是"本次意图"。
        self._exploring_idx: int | None = None
        #: 跨调用持久化的探索目标。一旦 _argmax_with_exploration 选定一个
        #: 桶要"凑样本"，此字段持续指向该桶，直到：
        #:   1) 桶样本数 ≥ MIN_SAMPLES_PER_BUCKET（达成覆盖），或
        #:   2) EXPLORATION_TIMEOUT_S 超时（segmenter buffer 常年凑不齐）
        #: 与 _exploring_idx 的区别：后者每次 _compute_l_star 重算可能被
        #: OCR 瓶颈分支清零，前者必须显式满足完成条件才清。
        self._pending_exploration: int | None = None
        self._pending_exploration_started_at: float | None = None
        #: 最近一次 record_llm 时使用的 target（controller 实际"下单"成功
        #: 的段长）。MAX_RATE_CHANGE 限幅以此为 anchor，而不是 _last_target
        #: —— 避免"target_segment_chars 被 query 但 extract 失败没 record"
        #: 的空 query 也把 anchor 一路衰减到 MIN_CHARS，导致桶 0 霸权。
        self._last_recorded_target: int | None = None

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

    def record_llm(
        self,
        chars: int,
        duration: float,
        *,
        target: int | None = None,
    ) -> None:
        """登记一段 LLM refine 完成：chars 输入、duration 耗时。

        target 非空时按 target 归桶（表达"我试图探索哪个桶"），而不是按
        实际 chars —— 避免 segmenter 把 target=5250 切出 3000 字符段时，
        样本被错归到小桶，导致大桶的探索意图长期无法积累样本。r_ema 仍用
        chars/duration 反映真实观测吞吐；只是桶归属按意图。

        target=None 时（如 tail 段、外部测试）回退按 chars 归桶。
        """
        if chars <= 0 or duration <= 0:
            return

        # 1. 原始样本入滑窗 —— 保留给线性回归做 overhead/k 估计
        self._llm_samples.append((chars, duration))
        if len(self._llm_samples) > self.LLM_SAMPLE_WINDOW:
            self._llm_samples = (
                self._llm_samples[-self.LLM_SAMPLE_WINDOW:]
            )
        self._try_regress()

        # 2. 桶吞吐统计：异常样本只计入原始 samples（供线性回归），
        #    不污染 r_ema（拐点判定基准）
        bucket_key = target if target is not None and target > 0 else chars
        b = self._bucket_of(bucket_key)
        is_outlier = self._is_outlier(b, duration)
        if is_outlier:
            self._outlier_count += 1
        else:
            r = chars / duration
            self._bucket_r_ema[b] = self._update_ema(
                self._bucket_r_ema[b], r,
            )
            self._bucket_med_t[b] = self._update_ema(
                self._bucket_med_t[b], duration,
            )
            self._bucket_count[b] += 1

        # 3. MAX_RATE_CHANGE anchor 更新：只有真实 record 才推进 anchor，
        #    空 query（target 返回但 extract 失败没 record）不算数，防止
        #    anchor 在"没有真实样本反馈"的情况下被衰减到 MIN_CHARS。
        if target is not None and target > 0:
            self._last_recorded_target = target

        # 4. 探索意图完成判定：若正在等 bucket X 凑满，且它现在达标，清零
        if (
            self._pending_exploration is not None
            and self._bucket_count[self._pending_exploration]
            >= self.MIN_SAMPLES_PER_BUCKET
        ):
            self._pending_exploration = None
            self._pending_exploration_started_at = None

        self._samples_since_reexplore += 1

        # 3. 冷启动完成判定
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

        # 检查 pending 探索是否超时：buffer 长期凑不齐大 target 时放弃
        self._expire_pending_exploration_if_timeout()

        # Pending 探索优先：上一次某次调用决定要"凑 bucket X 的样本"，
        # 本次仍未完成就继续返回该桶中心。跳过 _compute_l_star 以防 OCR
        # 瓶颈分支把意图擦掉；跳过 MAX_RATE_CHANGE 让探索目标一步到位。
        if self._pending_exploration is not None:
            idx = self._pending_exploration
            self._exploring_idx = idx
            target = self._bucket_center(idx)
            target = max(self.MIN_CHARS, min(self.MAX_CHARS, target))
            self._last_target = target
            return target

        target = self._compute_l_star()
        # 探索跳变（含爬山上探和周期重探）允许一次性跨桶，不受 ±30% 限幅，
        # 否则从冷启动末值到更大桶中心要好几轮才能到达，探索实际失效。
        # 稳态下仍然限幅防止 argmax 在两桶间抖动或异常样本把 L* 甩飞。
        if self._exploring_idx is None:
            # anchor 优先取 _last_recorded_target（真实反馈点）；首批调用
            # 尚无 record 时回退到 _last_target 保持旧行为兼容。
            anchor = (
                self._last_recorded_target
                if self._last_recorded_target is not None
                else self._last_target
            )
            cap_up = int(anchor * (1 + self.MAX_RATE_CHANGE))
            cap_down = int(anchor * (1 - self.MAX_RATE_CHANGE))
            target = max(cap_down, min(cap_up, target))
        target = max(self.MIN_CHARS, min(self.MAX_CHARS, target))
        self._last_target = target
        return target

    def _expire_pending_exploration_if_timeout(self) -> None:
        """若 pending 探索已超过 EXPLORATION_TIMEOUT_S 仍没被 record，放弃。"""
        if self._pending_exploration is None:
            return
        started = self._pending_exploration_started_at
        if started is None:
            return
        if time.monotonic() - started > self.EXPLORATION_TIMEOUT_S:
            logger.info(
                "RateController 放弃 pending 探索 bucket=%d (超时 %.1fs)",
                self._pending_exploration,
                self.EXPLORATION_TIMEOUT_S,
            )
            self._pending_exploration = None
            self._pending_exploration_started_at = None

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

    def snapshot(self) -> dict[str, Any]:
        """返回当前状态快照，写入 profile.json 供复盘。

        值类型宽（float / int / bool / list / Optional[int]），统一用 Any
        避免强类型噪声。
        """
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
            # 拐点学习观测量（v2 新加）
            "best_bucket_idx": self._best_bucket_idx,
            "exploring_idx": self._exploring_idx,
            "pending_exploration": self._pending_exploration,
            "last_recorded_target": self._last_recorded_target,
            "outlier_count": self._outlier_count,
            "bucket_edges": list(self.BUCKET_EDGES),
            "bucket_count": list(self._bucket_count),
            "bucket_r_ema": [
                round(r, 2) if r is not None else None
                for r in self._bucket_r_ema
            ],
            "bucket_med_t": [
                round(t, 2) if t is not None else None
                for t in self._bucket_med_t
            ],
        }

    # ── 内部：桶管理 ──────────────────────────────────────

    def _bucket_of(self, chars: int) -> int:
        """把 chars 映射到桶索引 [0, n_buckets)。"""
        # bisect_right: chars=3000 → idx 2 (落在 [3000, 4500) 桶)
        # 3000 属于第 2 个桶（索引 1），所以用 bisect_right - 1
        idx = bisect.bisect_right(self.BUCKET_EDGES, chars) - 1
        n_buckets = len(self.BUCKET_EDGES) - 1
        return max(0, min(n_buckets - 1, idx))

    def _bucket_center(self, idx: int) -> int:
        """桶中心字符数（作为目标段长）。"""
        lo = self.BUCKET_EDGES[idx]
        hi = self.BUCKET_EDGES[idx + 1]
        return (lo + hi) // 2

    def _is_outlier(self, bucket: int, duration: float) -> bool:
        """判定本次 (chars, duration) 样本是否该桶内的异常值。

        - 桶样本不足 MIN_SAMPLES_PER_BUCKET → 还没有基线，不过滤
        - 有基线且 duration > med_t × OUTLIER_MULTIPLIER → 异常
        """
        if self._bucket_count[bucket] < self.MIN_SAMPLES_PER_BUCKET:
            return False
        med = self._bucket_med_t[bucket]
        if med is None or med <= 0:
            return False
        return duration > med * self.OUTLIER_MULTIPLIER

    # ── 内部：回归与 L* 计算 ───────────────────────────────

    def _update_ema(
        self, prev: float | None, new_sample: float,
    ) -> float:
        """EMA 更新：prev 为空时直接用 new_sample 初始化。"""
        if prev is None:
            return new_sample
        return prev * (1 - self.EMA_ALPHA) + new_sample * self.EMA_ALPHA

    def _try_regress(self) -> None:
        """最小二乘回归 `duration = overhead + k · chars`。

        样本太少或 x 全相同 → 回退到简单比例估算（overhead=0）。
        线性回归仍保留：用来判别 OCR 瓶颈分支 + snapshot 诊断信息。
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
        """计算目标段长 L*。

        分支:
        - OCR 瓶颈（解析解 ∈ [MIN, MAX)）→ 用解析解做吞吐匹配
        - LLM 瓶颈 / legacy 失效（解析解 < MIN 或 ≥ MAX）→ argmax 爬山

        为什么 legacy_l < MIN_CHARS 要走 argmax 而不是 clamp 到 MIN？
        早期 OCR 样本少、EMA 未稳定时 R_ocr 估得很小，legacy 会输出
        几百字符甚至负值。此时若 clamp 到 MIN=1500，会让 target 长期
        停在下限，record_llm 全部样本涌入桶 0 形成自我强化的霸权，
        爬山永远无法探到更大桶。改走 argmax 让桶统计数据驱动决策，
        避免 OCR 冷启动期污染 L* 决策。
        """
        legacy_l = self._legacy_analytical_l_star()

        # OCR 瓶颈：LLM 还没成为约束，直接用解析解让两侧平衡
        if (
            legacy_l is not None
            and self.MIN_CHARS <= legacy_l < self.MAX_CHARS
        ):
            # 走经典分支就"放弃"爬山探索状态；保留桶数据以备切换回来
            self._exploring_idx = None
            return legacy_l

        # LLM 瓶颈 / legacy 失效：argmax + 爬山探索
        return self._argmax_with_exploration()

    def _legacy_analytical_l_star(self) -> int | None:
        """原吞吐匹配解析解 L* = R_ocr · overhead / (1 - R_ocr · k)。

        - 返回 None：数据不够算 / 解析解无意义（≤0），让上层走 argmax
        - 返回 ≥ MAX：落入 LLM 瓶颈分支
        - 返回有限正数：OCR 瓶颈，调用方按 [MIN, MAX) 接受
        """
        if (
            self._ocr_ema is None
            or self._chars_per_page_ema is None
            or self._overhead is None
            or self._k is None
        ):
            return None
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
            # overhead ≈ 0 + R_ocr 很慢 → 解析解退化，让上层 fallback argmax
            return None
        return int(l_star)

    def _argmax_with_exploration(self) -> int:
        """LLM 瓶颈场景的核心：分桶吞吐 argmax + 爬山探索拐点。

        三阶段:
        A. 覆盖扫描：bucket 0..best+1 内第一个 count < MIN_SAMPLES_PER_BUCKET
           的桶要先凑满，避免跳过中间桶
        B. 爬山上探：best_idx 右边有未探索桶 → 探该桶；有探但 r 跌破
           `best_r × KNEE_DROP_RATIO` → 锁定 best_idx
        C. 稳态 + 重探索：argmax 决定 L*；每 REEXPLORE_EVERY 段去试一次
           best_idx+1 探测拐点漂移

        argmax qualified 门槛（MIN_SAMPLES_FOR_ARGMAX=1）比覆盖探索门槛
        （MIN_SAMPLES_PER_BUCKET=2）低：单样本桶立刻进 best 候选池，驱动
        Phase A/B 把真正的最优桶凑满。
        """
        n_buckets = len(self.BUCKET_EDGES) - 1

        # best 候选池：count ≥ 1 就进，让单样本观测的高吞吐桶也能拿到
        # best_idx，进而通过 Phase A/B 逼近 argmax 真正最优点
        qualified = [
            i for i in range(n_buckets)
            if (
                self._bucket_count[i] >= self.MIN_SAMPLES_FOR_ARGMAX
                and self._bucket_r_ema[i] is not None
            )
        ]

        if qualified:
            best_idx = max(
                qualified,
                key=lambda i: self._bucket_r_ema[i] or 0.0,
            )
            self._best_bucket_idx = best_idx
        else:
            best_idx = self._best_bucket_idx

        # Phase A：覆盖扫描 bucket 0..best+1 —— 任何 < MIN_SAMPLES_PER_BUCKET
        # 的桶都要先凑满，确保 argmax 判断基于对中间桶的真实观测
        explore_up_to = min(best_idx + 1, n_buckets - 1)
        for i in range(explore_up_to + 1):
            if self._bucket_count[i] < self.MIN_SAMPLES_PER_BUCKET:
                self._set_pending_exploration(i)
                self._exploring_idx = i
                return self._bucket_center(i)

        # Phase B：爬山上探 best+1
        next_idx = best_idx + 1
        if next_idx < n_buckets:
            next_count = self._bucket_count[next_idx]
            next_r = self._bucket_r_ema[next_idx]
            if next_count < self.MIN_SAMPLES_PER_BUCKET:
                self._set_pending_exploration(next_idx)
                self._exploring_idx = next_idx
                return self._bucket_center(next_idx)
            best_r = self._bucket_r_ema[best_idx] or 0.0
            if (
                next_r is not None
                and next_r >= best_r * self.KNEE_DROP_RATIO
            ):
                # 右边桶没比 best 差多少，说明还没过拐点，继续试更大
                next_next = next_idx + 1
                if (
                    next_next < n_buckets
                    and self._bucket_count[next_next]
                    < self.MIN_SAMPLES_PER_BUCKET
                ):
                    self._set_pending_exploration(next_next)
                    self._exploring_idx = next_next
                    return self._bucket_center(next_next)

        # Phase C：稳态 —— best_idx 锁定；按周期重探索下一个桶
        self._exploring_idx = None
        if (
            self._samples_since_reexplore >= self.REEXPLORE_EVERY
            and next_idx < n_buckets
        ):
            self._samples_since_reexplore = 0
            self._set_pending_exploration(next_idx)
            self._exploring_idx = next_idx
            return self._bucket_center(next_idx)

        return self._bucket_center(best_idx)

    def _set_pending_exploration(self, idx: int) -> None:
        """登记 pending 探索目标；若已指向同一桶则保留原起始时间。"""
        if self._pending_exploration != idx:
            self._pending_exploration = idx
            self._pending_exploration_started_at = time.monotonic()
