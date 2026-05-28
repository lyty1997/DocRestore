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

"""LLMCache 单元测试

覆盖：
- 段级 / 整文档级的 get-put round trip
- 同 text 不同 model / api_base 不得误命中
- truncated=True 的结果绝不落盘（resume 时必须重试）
- enabled=False 行为等价于"永远 miss / 永不 put"
- 配置变化后的 invalidation（换 model 后原 key miss）
- 缓存目录创建失败时静默降级
"""

from __future__ import annotations

from pathlib import Path

from docrestore.llm.cache import LLMCache
from docrestore.models import Gap, RefinedResult


def _make_result(
    md: str = "refined",
    *,
    truncated: bool = False,
    gap_count: int = 0,
) -> RefinedResult:
    gaps = [
        Gap(
            after_image=f"{i}.jpg",
            context_before="pre",
            context_after="post",
        )
        for i in range(gap_count)
    ]
    return RefinedResult(markdown=md, gaps=gaps, truncated=truncated)


class TestSegmentCache:
    def test_put_then_get_roundtrip(self, tmp_path: Path) -> None:
        cache = LLMCache(tmp_path / ".llm_cache")
        assert cache.enabled
        r = _make_result("refined text", gap_count=2)
        cache.put_segment(
            model="openai/glm-5", api_base="", text="raw", result=r,
        )
        hit = cache.get_segment(
            model="openai/glm-5", api_base="", text="raw",
        )
        assert hit is not None
        assert hit.markdown == "refined text"
        assert len(hit.gaps) == 2
        assert hit.gaps[0].after_image == "0.jpg"
        assert hit.truncated is False

    def test_different_text_misses(self, tmp_path: Path) -> None:
        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_segment(
            model="m", api_base="", text="A", result=_make_result(),
        )
        assert cache.get_segment(
            model="m", api_base="", text="B",
        ) is None

    def test_different_model_misses(self, tmp_path: Path) -> None:
        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_segment(
            model="m1", api_base="", text="X", result=_make_result(),
        )
        assert cache.get_segment(
            model="m2", api_base="", text="X",
        ) is None

    def test_different_api_base_misses(self, tmp_path: Path) -> None:
        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_segment(
            model="m", api_base="https://a/v1",
            text="X", result=_make_result(),
        )
        assert cache.get_segment(
            model="m", api_base="https://b/v1", text="X",
        ) is None

    def test_truncated_result_never_stored(self, tmp_path: Path) -> None:
        """这是关键不变式：截断结果写进去 → resume 永远拿半截输出。"""
        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_segment(
            model="m", api_base="",
            text="raw",
            result=_make_result("half", truncated=True),
        )
        assert cache.get_segment(
            model="m", api_base="", text="raw",
        ) is None


class TestFinalCache:
    def test_put_then_get_roundtrip(self, tmp_path: Path) -> None:
        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_final(
            model="m", api_base="",
            markdown="raw doc",
            result=_make_result("clean doc"),
        )
        hit = cache.get_final(
            model="m", api_base="", markdown="raw doc",
        )
        assert hit is not None
        assert hit.markdown == "clean doc"

    def test_segment_and_final_are_separate_namespaces(
        self, tmp_path: Path,
    ) -> None:
        """同 text 分别写 segment 和 final，查询互不干扰。"""
        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_segment(
            model="m", api_base="", text="same",
            result=_make_result("seg_out"),
        )
        cache.put_final(
            model="m", api_base="", markdown="same",
            result=_make_result("final_out"),
        )
        seg_hit = cache.get_segment(
            model="m", api_base="", text="same",
        )
        fin_hit = cache.get_final(
            model="m", api_base="", markdown="same",
        )
        assert seg_hit is not None
        assert seg_hit.markdown == "seg_out"
        assert fin_hit is not None
        assert fin_hit.markdown == "final_out"


class TestDisabled:
    def test_disabled_never_hits(self, tmp_path: Path) -> None:
        cache = LLMCache(tmp_path / ".llm_cache", enabled=False)
        cache.put_segment(
            model="m", api_base="", text="X",
            result=_make_result(),
        )
        assert cache.get_segment(
            model="m", api_base="", text="X",
        ) is None
        # 目录不应被创建（disabled 完全无副作用）
        assert not (tmp_path / ".llm_cache").exists()


class TestCorruptedCacheFile:
    def test_broken_json_is_treated_as_miss(
        self, tmp_path: Path,
    ) -> None:
        """手工塞一个坏 json 文件，读取应静默降级为 miss。"""
        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_segment(
            model="m", api_base="", text="valid",
            result=_make_result(),
        )
        # 找到刚写入的文件，破坏内容
        files = list((tmp_path / ".llm_cache").glob("seg_*.json"))
        assert len(files) == 1
        files[0].write_text("{ not json", encoding="utf-8")
        assert cache.get_segment(
            model="m", api_base="", text="valid",
        ) is None


class TestPromptFingerprint:
    def test_prompt_change_invalidates(
        self, tmp_path: Path, monkeypatch: object,
    ) -> None:
        """monkeypatch REFINE_SYSTEM_PROMPT 改动后，原 key 不再命中。

        防止"改 prompt 后仍拿旧结果"的陷阱。
        """
        from docrestore.llm import prompts

        cache = LLMCache(tmp_path / ".llm_cache")
        cache.put_segment(
            model="m", api_base="", text="raw",
            result=_make_result("with_v1_prompt"),
        )
        # hit
        assert cache.get_segment(
            model="m", api_base="", text="raw",
        ) is not None

        # 改 prompt → 新 fingerprint → miss
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(
                prompts, "REFINE_SYSTEM_PROMPT",
                "TOTALLY DIFFERENT PROMPT",
            )
            assert cache.get_segment(
                model="m", api_base="", text="raw",
            ) is None
        finally:
            mp.undo()
