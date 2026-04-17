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

"""Profiler 单元测试"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from docrestore.pipeline.profiler import (
    MemoryProfiler,
    NullProfiler,
    create_profiler,
)


class TestNullProfiler:
    """禁用路径：所有方法 no-op，零开销。"""

    def test_stage_is_noop(self) -> None:
        """stage() 不抛异常，上下文正常进出。"""
        p = NullProfiler()
        with p.stage("foo", bar=1):
            pass

    def test_record_external_is_noop(self) -> None:
        p = NullProfiler()
        p.record_external("ext", 0.5, attr="x")  # 不抛

    def test_export_summary_returns_empty(self) -> None:
        p = NullProfiler()
        assert p.export_summary_table() == ""

    def test_export_json_is_noop(self, tmp_path: Path) -> None:
        """禁用时 export_json 不写文件。"""
        p = NullProfiler()
        out = tmp_path / "profile.json"
        p.export_json(out)
        assert not out.exists()

    def test_stage_overhead_is_low(self) -> None:
        """NullProfiler.stage() 单次调用应为纳秒级。

        预算：10000 次调用总耗时 < 50ms（单次 < 5μs，NullCtx 实际远更快）。
        """
        p = NullProfiler()
        t0 = time.monotonic()
        for _ in range(10000):
            with p.stage("hot"):
                pass
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05, f"NullProfiler 过慢: {elapsed:.3f}s / 10k 次"


class TestMemoryProfiler:
    """启用路径：事件收集 + 导出。"""

    def test_records_stage_duration(self) -> None:
        p = MemoryProfiler()
        with p.stage("work"):
            time.sleep(0.01)
        table = p.export_summary_table()
        assert "work" in table
        assert "1" in table  # count

    def test_nested_stages_have_correct_depth(self) -> None:
        p = MemoryProfiler()
        with p.stage("outer"):
            with p.stage("middle"):
                with p.stage("inner"):
                    pass

        # 按 name 找事件
        events = {e.name: e for e in p._events}
        assert events["outer"].depth == 0
        assert events["middle"].depth == 1
        assert events["inner"].depth == 2

    def test_sibling_stages_share_depth(self) -> None:
        p = MemoryProfiler()
        with p.stage("parent"):
            with p.stage("child_a"):
                pass
            with p.stage("child_b"):
                pass

        events = [e for e in p._events if e.name in {"child_a", "child_b"}]
        assert all(e.depth == 1 for e in events)

    def test_attrs_are_preserved(self) -> None:
        p = MemoryProfiler()
        with p.stage("s", batch_size=4, image="a.jpg"):
            pass
        e = p._events[0]
        assert e.attrs == {"batch_size": 4, "image": "a.jpg"}

    def test_exception_in_stage_still_records(self) -> None:
        """stage 内抛异常仍应正确记录事件。"""
        p = MemoryProfiler()
        with pytest.raises(ValueError, match="boom"):
            with p.stage("failing"):
                raise ValueError("boom")

        assert len(p._events) == 1
        assert p._events[0].name == "failing"
        # 栈正确弹空
        assert len(p._depth_stack) == 0

    def test_record_external_uses_current_depth(self) -> None:
        p = MemoryProfiler()
        with p.stage("outer"):
            p.record_external("gpu_infer", 0.123, image="a.jpg")
        # outer 事件 + 外部事件
        assert len(p._events) == 2
        external = next(e for e in p._events if e.name == "gpu_infer")
        assert external.depth == 1  # 在 outer 内
        assert external.duration_s == 0.123

    def test_export_json_roundtrip(self, tmp_path: Path) -> None:
        p = MemoryProfiler()
        with p.stage("pipeline.total", task_id="t1"):
            with p.stage("ocr.phase", num_images=5):
                pass

        out = tmp_path / "sub" / "profile.json"  # 测试父目录自动创建
        p.export_json(out)
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == 2
        names = {d["name"] for d in data}
        assert names == {"pipeline.total", "ocr.phase"}

    def test_summary_share_uses_pipeline_total(self) -> None:
        """share% 以 pipeline.total 为 100%。"""
        p = MemoryProfiler()
        with p.stage("pipeline.total"):
            with p.stage("ocr.phase"):
                time.sleep(0.02)
            # 让 pipeline.total 稍长于 ocr.phase
            time.sleep(0.01)

        table = p.export_summary_table()
        # pipeline.total 应显示 100.0%
        lines = [line for line in table.splitlines() if "pipeline.total" in line]
        assert lines
        assert "100.0%" in lines[0]

    def test_summary_empty_returns_empty(self) -> None:
        assert MemoryProfiler().export_summary_table() == ""


class TestCreateProfiler:
    """工厂函数行为。"""

    def test_disabled_returns_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DOCRESTORE_PROFILING", raising=False)
        p = create_profiler(enable=False)
        assert isinstance(p, NullProfiler)

    def test_enabled_returns_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DOCRESTORE_PROFILING", raising=False)
        p = create_profiler(enable=True)
        assert isinstance(p, MemoryProfiler)

    @pytest.mark.parametrize("env_val", ["1", "true", "yes", "on"])
    def test_env_var_overrides_disable(
        self, monkeypatch: pytest.MonkeyPatch, env_val: str,
    ) -> None:
        """DOCRESTORE_PROFILING=1 即使 enable=False 也启用。"""
        monkeypatch.setenv("DOCRESTORE_PROFILING", env_val)
        p = create_profiler(enable=False)
        assert isinstance(p, MemoryProfiler)

    def test_env_var_off_keeps_disable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DOCRESTORE_PROFILING", "0")
        p = create_profiler(enable=False)
        assert isinstance(p, NullProfiler)
