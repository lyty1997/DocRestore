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
        """解析解远小于冷启动末值时，首次自适应被 cap_down 限幅（±30%）。"""
        ctrl = RateController()
        # duration = 0.5 + 0.001 · chars → overhead=0.5, k=0.001
        # OCR 5s/页 · 2000 字符 → R_ocr = 400 chars/s
        # R_ocr · k = 0.4，denom = 0.6
        # L* 解析解 = 400 · 0.5 / 0.6 ≈ 333
        # 但首次自适应 last_target bump 到冷启动末值 6000，cap_down = 4200
        # → 最终 target = 4200
        self._seed_samples(
            ctrl,
            samples=[
                (2000, 2.5),
                (4000, 4.5),
                (6000, 6.5),
            ],
            ocr_duration=5.0,
            chars_per_page=2000,
        )
        target = ctrl.target_segment_chars()
        cap_down_from_6000 = int(
            RateController.COLD_START_SEQUENCE[-1]
            * (1 - RateController.MAX_RATE_CHANGE),
        )
        assert target == cap_down_from_6000

    def test_llm_bottleneck_converges_to_max(self) -> None:
        """R_ocr · k ≥ 1 时 L* 向 MAX 收敛；每次最多 +30%，多轮到 MAX。"""
        ctrl = RateController()
        # duration = 0.1 + 0.01 · chars → k=0.01
        # OCR 1s/页 · 200 chars → R_ocr = 200 chars/s
        # R_ocr · k = 2.0 > 1 → 解析解返回 MAX
        self._seed_samples(
            ctrl,
            samples=[
                (1000, 10.1),
                (2000, 20.1),
                (3000, 30.1),
            ],
            ocr_duration=1.0,
            chars_per_page=200,
        )
        # 多次 query 让变化率限幅阶梯式爬升到 MAX
        last = 0
        for _ in range(20):
            last = ctrl.target_segment_chars()
            if last >= RateController.MAX_CHARS:
                break
        assert last == RateController.MAX_CHARS

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
        }
        assert expected_keys.issubset(snap.keys())
        assert snap["samples_llm"] == 1
        assert snap["ocr_avg_s"] == pytest.approx(1.0)
