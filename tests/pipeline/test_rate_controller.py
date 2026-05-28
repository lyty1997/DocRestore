# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""RateController 单元测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from docrestore.pipeline.rate_controller import RateController


class TestColdStartSequence:
    """样本不足时按动态序列出 target。"""

    def test_initial_target(self) -> None:
        ctrl = RateController()
        assert ctrl.target_segment_chars() == 1500

    def test_after_one_sample(self) -> None:
        ctrl = RateController()
        ctrl.record_llm(chars=1500, duration=3.0)
        assert ctrl.target_segment_chars() == 3000

    def test_after_two_samples(self) -> None:
        ctrl = RateController()
        ctrl.record_llm(chars=1500, duration=3.0)
        ctrl.record_llm(chars=3000, duration=5.0)
        assert ctrl.target_segment_chars() == 6000

    def test_zero_or_negative_samples_ignored(self) -> None:
        ctrl = RateController()
        ctrl.record_llm(chars=0, duration=1.0)
        ctrl.record_llm(chars=1000, duration=-1.0)
        assert ctrl.target_segment_chars() == 1500


class TestColdStartDoneEvent:
    """3 个 LLM 样本后触发 cold_start_done。"""

    def test_done_after_three_samples(self) -> None:
        ctrl = RateController()
        assert not ctrl.cold_start_done.is_set()
        ctrl.record_llm(chars=1500, duration=3.0)
        ctrl.record_llm(chars=3000, duration=5.0)
        assert not ctrl.cold_start_done.is_set()
        ctrl.record_llm(chars=6000, duration=8.0)
        assert ctrl.cold_start_done.is_set()


class TestAdaptiveLStar:
    """样本 ≥ 3 后按解析解 L* = R_ocr · overhead / (1 - R_ocr · k)。"""

    def _seed_samples(
        self,
        ctrl: RateController,
        samples: list[tuple[int, float]],
        ocr_duration: float,
        chars_per_page: int,
    ) -> None:
        for chars, dur in samples:
            ctrl.record_llm(chars=chars, duration=dur)
        # OCR 样本：给 EMA 一个稳定值
        for _ in range(5):
            ctrl.record_ocr(duration=ocr_duration, chars=chars_per_page)

    def test_l_star_analytical_hit_cap_down(self) -> None:
        """解析解 ∈ [MIN, MAX) 且小于冷启动末值时，首次自适应被 cap_down 限幅。

        Legacy 返回值必须 ≥ MIN_CHARS 才走 legacy 分支；若 < MIN，新语义
        改走 argmax（见 test_legacy_below_min_falls_back_to_argmax）。
        """
        ctrl = RateController()
        # duration = 5 + 0.002 · chars → overhead=5, k=0.002
        # OCR 1s/页 · 300 字符 → R_ocr=300, R_ocr·k=0.6
        # L* = 300 · 5 / 0.4 = 3750 (在 [MIN, MAX) 内，触发 legacy 分支)
        # 首次自适应 last_target bump 到 6000, cap_down = 4200
        # → clamp(3750, 4200, 7800) = 4200
        self._seed_samples(
            ctrl,
            samples=[
                (2000, 9.0),
                (4000, 13.0),
                (6000, 17.0),
            ],
            ocr_duration=1.0,
            chars_per_page=300,
        )
        target = ctrl.target_segment_chars()
        cap_down_from_6000 = int(
            RateController.COLD_START_SEQUENCE[-1]
            * (1 - RateController.MAX_RATE_CHANGE),
        )
        assert target == cap_down_from_6000

    def test_llm_bottleneck_converges_to_max(self) -> None:
        """线性 LLM（R_llm 单调增）+ LLM 瓶颈 → argmax 爬山到最大桶。

        每次 target_segment_chars() 查询后按线性模型模拟一次 LLM 调用
        并 record_llm 回馈，直到爬山探索到最大桶并锁定。
        """
        ctrl = RateController()
        # duration = 0.1 + 0.01 · chars（严格线性）→ R_llm(L) 单调增到 1/k=100
        # OCR 1s/页 · 200 chars → R_ocr = 200 chars/s，R_ocr · k = 2 > 1
        # → LLM 瓶颈，走 argmax + 爬山分支
        for chars in RateController.COLD_START_SEQUENCE:
            ctrl.record_llm(chars=chars, duration=0.1 + 0.01 * chars)
        for _ in range(5):
            ctrl.record_ocr(duration=1.0, chars=200)

        # 模拟真实调度：query → 按 target 做一次"LLM 调用"→ 回馈样本
        last = 0
        for _ in range(60):
            target = ctrl.target_segment_chars()
            ctrl.record_llm(
                chars=target, duration=0.1 + 0.01 * target,
            )
            last = target
        # 最大桶 [10000, 12000] 中心 = 11000；允许 ±500 抖动
        max_bucket_center = (
            RateController.BUCKET_EDGES[-2]
            + RateController.BUCKET_EDGES[-1]
        ) // 2
        assert abs(last - max_bucket_center) <= 500, (
            f"线性 LLM 应爬到最大桶中心 {max_bucket_center}，实际 {last}"
        )

    def test_rate_change_cap(self) -> None:
        """单次 L* 变化不超过 ±30%，防震荡。"""
        ctrl = RateController()
        self._seed_samples(
            ctrl,
            samples=[
                (1000, 1.0), (2000, 1.5), (3000, 2.0),
            ],
            ocr_duration=0.5,
            chars_per_page=500,
        )
        # last_target 是冷启动末值（6000 或解析解），先触发一次读取
        t1 = ctrl.target_segment_chars()
        # 突然喂一批"LLM 飞慢"的样本，目标理论上想暴涨到 MAX
        for _ in range(10):
            ctrl.record_llm(chars=2000, duration=60.0)
        t2 = ctrl.target_segment_chars()
        ratio = t2 / t1 if t1 > 0 else 0
        assert 0.7 - 0.01 <= ratio <= 1.3 + 0.01, (
            f"变化率超出 ±30%: {t1} → {t2}"
        )


class TestWaitColdStart:
    """wait_cold_start 超时 fallback 行为。"""

    @pytest.mark.asyncio
    async def test_returns_immediately_if_already_done(self) -> None:
        ctrl = RateController()
        ctrl.record_llm(chars=1000, duration=1.0)
        ctrl.record_llm(chars=2000, duration=2.0)
        ctrl.record_llm(chars=3000, duration=3.0)
        # 立即 done
        await asyncio.wait_for(ctrl.wait_cold_start(), timeout=0.5)

    @pytest.mark.asyncio
    async def test_timeout_fallback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """超时后 cold_start_done 被强制 set，snapshot 标记 failed。"""
        monkeypatch.setattr(
            RateController, "COLD_START_TIMEOUT_S", 0.05,
        )
        ctrl = RateController()
        await ctrl.wait_cold_start()
        assert ctrl.cold_start_done.is_set()
        assert ctrl.snapshot()["cold_start_failed"] is True


class TestArgmaxExploration:
    """argmax + 爬山探索分支（LLM 瓶颈 / 非线性拐点）。"""

    @staticmethod
    def _drive_until_stable(
        ctrl: RateController,
        simulate: Callable[[int], float],
        n_iters: int = 60,
    ) -> list[int]:
        """反复 query → simulate(target) → record_llm，收集 target 轨迹。"""
        trajectory: list[int] = []
        for _ in range(n_iters):
            target = ctrl.target_segment_chars()
            dur = simulate(target)
            ctrl.record_llm(chars=target, duration=dur)
            trajectory.append(target)
        return trajectory

    def test_nonlinear_knee_at_6000_locks_best(self) -> None:
        """非线性拐点 LLM：L>6000 后耗时超线性变慢 → argmax 锁定 6000 附近桶。"""
        ctrl = RateController()
        # 冷启动 3 样本：按序列喂，避让拐点
        for chars in RateController.COLD_START_SEQUENCE:
            ctrl.record_llm(chars=chars, duration=0.1 + 0.01 * chars)
        for _ in range(5):
            ctrl.record_ocr(duration=1.0, chars=200)  # LLM 瓶颈

        def simulate(L: int) -> float:
            # L ≤ 6000 线性；L > 6000 额外平方增长，R_llm 在 L>6000 后下降
            base = 0.1 + 0.01 * L
            if L <= 6000:
                return base
            extra = ((L - 6000) / 1000) ** 2  # 7k → 1s、10k → 16s、12k → 36s
            return base + extra

        traj = self._drive_until_stable(ctrl, simulate, n_iters=80)
        # 最终几次都应稳定在拐点附近桶中心（6000 所在桶或 [6000,8000) 桶）
        final_tail = traj[-10:]
        # 允许在 [4500, 8000] 范围浮动，避免过窄假设；但不应到 10000+
        assert all(
            4500 <= t <= 8000 for t in final_tail
        ), f"末尾轨迹应在拐点附近，实际 {final_tail}"
        snap = ctrl.snapshot()
        # argmax 锁定的 best_bucket 应指向 [4500,6000) 或 [6000,8000)
        assert snap["best_bucket_idx"] in (2, 3), snap

    def test_outlier_is_filtered(self) -> None:
        """单次异常慢样本不会污染 r_ema / med_t。"""
        ctrl = RateController()
        # 先给桶 1 (3000-4500) 喂 3 条正常样本建立基线
        for _ in range(3):
            ctrl.record_llm(chars=3500, duration=5.0)
        snap_before = ctrl.snapshot()
        med_before = snap_before["bucket_med_t"][1]
        r_before = snap_before["bucket_r_ema"][1]
        # 喂一条异常样本（10× 正常耗时）
        ctrl.record_llm(chars=3500, duration=50.0)
        snap_after = ctrl.snapshot()
        # 异常被过滤：r_ema 和 med_t 几乎不动
        assert snap_after["bucket_med_t"][1] == med_before
        assert snap_after["bucket_r_ema"][1] == r_before
        assert snap_after["outlier_count"] == 1

    def test_outlier_not_filtered_during_cold_bucket(self) -> None:
        """桶内样本不足 MIN_SAMPLES_PER_BUCKET 时不做异常过滤（无基线）。"""
        ctrl = RateController()
        # 第一条就异常慢；桶内无基线无从判定，应正常计入
        ctrl.record_llm(chars=3500, duration=50.0)
        snap = ctrl.snapshot()
        assert snap["outlier_count"] == 0
        assert snap["bucket_count"][1] == 1

    def test_reexplore_after_period(self) -> None:
        """稳态达到 REEXPLORE_EVERY 段后，会重探 best_idx+1 桶。"""
        ctrl = RateController()
        # 构造稳态：让 best 锁定在桶 2 ([4500,6000))；桶 3 有少量样本但 r 低
        # 冷启动
        for chars in RateController.COLD_START_SEQUENCE:
            ctrl.record_llm(chars=chars, duration=0.1 + 0.01 * chars)
        for _ in range(5):
            ctrl.record_ocr(duration=1.0, chars=200)

        # 模拟：L=5000 非常快（r≈1000），L=7000 很慢（r≈100）
        def simulate(L: int) -> float:
            if L <= 6000:
                return L / 1000.0  # r = 1000 chars/s
            return L / 100.0  # r = 100 chars/s（掉下拐点）

        # 跑满 REEXPLORE_EVERY + 几步
        seen_reexplore = False
        for _ in range(RateController.REEXPLORE_EVERY + 5):
            target = ctrl.target_segment_chars()
            ctrl.record_llm(chars=target, duration=simulate(target))
            # 观测到 target 跳去桶 3 (6000-8000) 即重探索生效
            if 6000 <= target < 8000:
                seen_reexplore = True
        assert seen_reexplore, "稳态后应周期性重探更大桶"

    def test_bucket_boundary_classification(self) -> None:
        """边界字符应落入正确的桶（bisect_right 语义）。"""
        ctrl = RateController()
        # 边界：1500→桶0, 3000→桶1, 4500→桶2, 12000→最末桶
        cases = [
            (1500, 0), (2999, 0),
            (3000, 1), (4499, 1),
            (4500, 2), (5999, 2),
            (6000, 3), (7999, 3),
            (8000, 4), (9999, 4),
            (10000, 5), (12000, 5),
            # 越界: 超 MAX 归到最末桶，低于 MIN 归到首桶
            (50000, 5), (100, 0),
        ]
        for chars, expected in cases:
            assert ctrl._bucket_of(chars) == expected, (
                f"chars={chars} 应落桶 {expected}, "
                f"实际 {ctrl._bucket_of(chars)}"
            )

    def test_record_llm_bucket_by_target(self) -> None:
        """record_llm(target=...) 时按 target 归桶，而不是实际 chars。

        场景：controller 下发 target=7000（桶 3），但 segmenter 只切出
        3000 chars。样本应记到桶 3（探索意图），不能让桶 1 的样本污染
        探索计数。
        """
        ctrl = RateController()
        ctrl.record_llm(chars=3000, duration=5.0, target=7000)
        snap = ctrl.snapshot()
        # 桶 3 = [6000, 8000)，应收到这次样本
        assert snap["bucket_count"][3] == 1
        # 桶 1 ([3000, 4500)) 对应 chars=3000，不应该被计入
        assert snap["bucket_count"][1] == 0
        # r_ema 仍用实际 chars/duration
        assert snap["bucket_r_ema"][3] == round(3000 / 5.0, 2)

    def test_record_llm_target_none_fallback_to_chars(self) -> None:
        """target=None（如 tail 段）回退按 chars 归桶，保持向后兼容。"""
        ctrl = RateController()
        ctrl.record_llm(chars=3000, duration=5.0)
        snap = ctrl.snapshot()
        # chars=3000 属于桶 1
        assert snap["bucket_count"][1] == 1
        assert snap["bucket_count"][3] == 0

    def test_legacy_below_min_falls_back_to_argmax(self) -> None:
        """legacy 解析解 < MIN_CHARS 时不走 legacy，改走 argmax 桶探索。

        防止早期 OCR 冷启动样本不稳定（R_ocr 估得过小）导致 legacy
        输出几百字符，clamp 到 MIN=1500 → 形成桶 0 自我强化霸权。
        """
        ctrl = RateController()
        # 构造 legacy 会输出非常小的 L*：
        # 3 LLM 样本 k~0.001 overhead~0；OCR 很慢但 chars 也很小
        # → R_ocr · k 很小 ∈ (0, 1)，走 legacy，但 overhead → 0 让 L*→0
        for c, d in [(1500, 1.5), (3000, 3.0), (6000, 6.0)]:
            ctrl.record_llm(chars=c, duration=d)
        for _ in range(5):
            ctrl.record_ocr(duration=10.0, chars=100)
        # 此时线性回归 overhead ≈ 0, k ≈ 0.001
        # R_ocr = 100/10 = 10 chars/s, R_ocr·k ≈ 0.01 < 1
        # L* = 10 * 0 / 0.99 ≈ 0 → legacy 返回 None
        # → 应该走 argmax 分支而不是 clamp 到 1500
        target = ctrl.target_segment_chars()
        # argmax 分支在冷启动完成后会返回桶中心值（≥ bucket_center(0) = 2250）
        # 关键断言：target 不应该被压到 MIN_CHARS=1500
        assert target > RateController.MIN_CHARS, (
            f"legacy < MIN 时不应 clamp 到 MIN, 实际 target={target}"
        )


class TestSnapshot:
    """snapshot 字段齐全可序列化。"""

    def test_snapshot_keys(self) -> None:
        ctrl = RateController()
        ctrl.record_ocr(duration=1.0, chars=1000)
        ctrl.record_llm(chars=1500, duration=2.0)
        snap = ctrl.snapshot()
        expected_keys = {
            "ocr_avg_s", "chars_per_page_avg",
            "llm_overhead_s", "llm_per_char_s",
            "samples_llm", "cold_start_elapsed_s",
            "cold_start_failed", "final_target_chars",
            "queue_depth_last",
            # v3 新加字段（pending exploration / record target anchor）
            "pending_exploration", "last_recorded_target",
        }
        assert expected_keys.issubset(snap.keys())
        assert snap["samples_llm"] == 1
        assert snap["ocr_avg_s"] == pytest.approx(1.0)


class TestAntiBucketZeroHegemony:
    """桶 0 霸权回归测试（基于 2026-04-22 gpt-5.4-mini profile 观测）。

    观测：mini 跑完后 bucket_count=[50, 23, 1, 0, 1, 0]，bucket 2 r_ema=646
    是全局最高但 count=1 永远进不了 argmax（旧 MIN_SAMPLES_PER_BUCKET=2 门槛），
    target 被限幅一路衰减到 MIN_CHARS=1500 霸占 67% 样本。

    本组测试覆盖：
    1. pending 探索跨调用持久化，不被 OCR 瓶颈分支擦掉
    2. 单样本高吞吐桶能立刻参与 argmax
    3. MAX_RATE_CHANGE anchor 基于 record_llm 时点而不是 query 时点
    4. pending 探索超时能自动放弃
    """

    def test_pending_exploration_survives_query_burst(self) -> None:
        """空 query 不消耗探索意图：5 次无 record 的 query 仍应坚持探索目标。

        模拟多子目录并发场景：一次 query 选定 bucket 2（返回 5250），后续
        多次 query 因为 segmenter buffer 不够没有 record_llm。旧代码每次
        query 会重算 _compute_l_star，OCR 瓶颈分支一旦触发就把探索意图擦掉。
        """
        ctrl = RateController()
        # 冷启动 3 样本，形成 bucket 0/1/3 各 1 个
        for chars in RateController.COLD_START_SEQUENCE:
            ctrl.record_llm(chars=chars, duration=0.1 + 0.01 * chars)
        for _ in range(5):
            ctrl.record_ocr(duration=1.0, chars=200)

        # 第一次 query：应进入 Phase A 覆盖扫描，先探 bucket 0（count=1<2）
        t1 = ctrl.target_segment_chars()
        assert ctrl._pending_exploration is not None
        pending = ctrl._pending_exploration

        # 连续 5 次空 query（不 record），target 应持续指向同一桶中心
        for _ in range(5):
            t = ctrl.target_segment_chars()
            assert ctrl._pending_exploration == pending, (
                "pending 探索意图不应被空 query 擦掉"
            )
            assert t == t1, "空 query 时 target 应稳定"

    def test_single_sample_bucket_wins_argmax(self) -> None:
        """单样本桶吞吐观测很高时应被选为 best，不再卡在 MIN_SAMPLES=2 门槛。

        mini 场景缩影：bucket 0 多个样本但 r 中等，bucket 2 只有 1 样本但
        r 明显更高。新 argmax 应认可 bucket 2 为 best。
        """
        ctrl = RateController()
        for chars in RateController.COLD_START_SEQUENCE:
            ctrl.record_llm(chars=chars, duration=0.1 + 0.01 * chars)
        for _ in range(5):
            ctrl.record_ocr(duration=1.0, chars=200)

        # 给 bucket 0 灌 5 个中等吞吐样本（r ≈ 500）
        for _ in range(5):
            ctrl.record_llm(chars=2200, duration=4.4, target=2200)
        # 给 bucket 2 一个高吞吐样本（r ≈ 1000）
        ctrl.record_llm(chars=5200, duration=5.2, target=5250)

        # 触发一次 _compute_l_star 让 best_idx 更新
        ctrl.target_segment_chars()
        snap = ctrl.snapshot()
        assert snap["best_bucket_idx"] == 2, (
            f"单样本高吞吐桶应能赢 argmax，snapshot={snap}"
        )

    def test_max_rate_change_anchor_uses_recorded_target(self) -> None:
        """限幅 anchor 基于 _last_recorded_target 而非 _last_target。

        空 query 把 _last_target 衰减到 MIN，但 anchor 应跟 record_llm 走 —
        这正是"target_segment_chars 被 query 但 extract 失败"场景下防止
        桶 0 霸权的关键。
        """
        ctrl = RateController()
        for chars in RateController.COLD_START_SEQUENCE:
            ctrl.record_llm(chars=chars, duration=0.1 + 0.01 * chars)
        for _ in range(5):
            ctrl.record_ocr(duration=1.0, chars=300)

        # 真实 record 一次大 target，让 anchor 记在 5250
        ctrl.record_llm(chars=5200, duration=5.2, target=5250)
        assert ctrl._last_recorded_target == 5250

        # 多次空 query 不应把 anchor 拉低（只有 _last_target 会被本地衰减）
        for _ in range(10):
            ctrl.target_segment_chars()
        assert ctrl._last_recorded_target == 5250, (
            "空 query 不应改变 anchor"
        )

    def test_pending_exploration_times_out(self) -> None:
        """pending 探索超时后自动放弃，防止 buffer 长期凑不齐死循环。"""
        ctrl = RateController()
        ctrl.EXPLORATION_TIMEOUT_S = 0.05  # 缩短到 50ms 便于测试
        for chars in RateController.COLD_START_SEQUENCE:
            ctrl.record_llm(chars=chars, duration=0.1 + 0.01 * chars)
        for _ in range(5):
            ctrl.record_ocr(duration=1.0, chars=200)

        ctrl.target_segment_chars()
        assert ctrl._pending_exploration is not None

        import time as _t
        _t.sleep(0.08)  # 超过 EXPLORATION_TIMEOUT_S
        ctrl.target_segment_chars()
        # 超时后 pending 被清零（下一次调用可能又设新的，但至少不是原来那个卡死的）
        # 更严格：连续调用时至少有一次 pending=None 的窗口
        # 这里直接验证 expire 函数生效过
        assert (
            ctrl._pending_exploration is None
            or ctrl._pending_exploration_started_at is not None
            and (
                _t.monotonic() - ctrl._pending_exploration_started_at
            ) < 0.05
        ), "pending 探索应被超时清零或重置为新目标"
