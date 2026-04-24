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

"""QualityReport + 各阶段检测器单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docrestore.pipeline.quality_report import (
    QualityIssue,
    QualityReport,
    detect_cleaner_quality,
    detect_final_refine_quality,
    detect_llm_segment_quality,
    detect_merger_quality,
)


@pytest.mark.asyncio
async def test_add_fills_timestamp() -> None:
    report = QualityReport()
    await report.add(QualityIssue(
        stage="cleaner", code="test.demo", message="demo",
    ))
    assert len(report.issues) == 1
    assert report.issues[0].timestamp != ""


@pytest.mark.asyncio
async def test_summary_aggregates_counts() -> None:
    report = QualityReport()
    await report.add(QualityIssue(stage="cleaner", code="x", severity="warn"))
    await report.add(QualityIssue(stage="cleaner", code="y", severity="warn"))
    await report.add(QualityIssue(stage="llm_segment", code="z", severity="info"))
    summary = report.summary()
    assert summary["total"] == 3
    assert summary["by_stage"] == {"cleaner": 2, "llm_segment": 1}
    assert summary["by_severity"] == {"warn": 2, "info": 1}


@pytest.mark.asyncio
async def test_dump_to_file(tmp_path: Path) -> None:
    report = QualityReport()
    await report.add(QualityIssue(
        stage="cleaner", code="cleaner.test",
        severity="warn", message="msg", page="a.jpg",
    ))
    out = tmp_path / "quality.json"
    await report.dump_to_file(out)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 1
    assert data["issues"][0]["page"] == "a.jpg"


class TestDetectCleanerQuality:
    @pytest.mark.asyncio
    async def test_high_garbage_ratio_triggers(self) -> None:
        report = QualityReport()
        raw = "a" * 100
        cleaned = "a" * 70  # 30% removed
        await detect_cleaner_quality(
            report, "p1.jpg", raw, cleaned,
        )
        assert len(report.issues) == 1
        issue = report.issues[0]
        assert issue.code == "cleaner.high_garbage_ratio"
        assert issue.page == "p1.jpg"
        assert issue.metadata["removed_ratio"] == 0.3

    @pytest.mark.asyncio
    async def test_low_garbage_ratio_no_trigger(self) -> None:
        report = QualityReport()
        raw = "a" * 100
        cleaned = "a" * 95  # 5% removed
        await detect_cleaner_quality(report, "p1.jpg", raw, cleaned)
        assert len(report.issues) == 0

    @pytest.mark.asyncio
    async def test_near_empty_after_clean(self) -> None:
        report = QualityReport()
        raw = "a" * 200
        cleaned = "ab"
        await detect_cleaner_quality(report, "p1.jpg", raw, cleaned)
        assert len(report.issues) == 1
        assert report.issues[0].code == "cleaner.near_empty"


class TestDetectLLMSegmentQuality:
    @pytest.mark.asyncio
    async def test_fallback_to_raw_recorded(self) -> None:
        report = QualityReport()
        await detect_llm_segment_quality(
            report, segment_index=3,
            truncated=False, fallback_to_raw=True,
            output_markdown="irrelevant",
        )
        assert len(report.issues) == 1
        assert report.issues[0].code == "llm.seg_fallback_to_raw"
        assert report.issues[0].segment_index == 3

    @pytest.mark.asyncio
    async def test_truncated_recorded(self) -> None:
        report = QualityReport()
        await detect_llm_segment_quality(
            report, segment_index=1,
            truncated=True, fallback_to_raw=False,
            output_markdown="ok",
        )
        assert any(
            i.code == "llm.seg_truncated" for i in report.issues
        )

    @pytest.mark.asyncio
    async def test_ui_noise_residual_detected(self) -> None:
        report = QualityReport()
        md = "正文\nPlain Text 复制代码\n代码行\n"
        await detect_llm_segment_quality(
            report, segment_index=2,
            truncated=False, fallback_to_raw=False,
            output_markdown=md,
        )
        assert any(
            i.code == "llm.seg_ui_noise_residual" for i in report.issues
        )

    @pytest.mark.asyncio
    async def test_clean_output_no_issue(self) -> None:
        report = QualityReport()
        await detect_llm_segment_quality(
            report, segment_index=1,
            truncated=False, fallback_to_raw=False,
            output_markdown="## 标题\n正文内容\n",
        )
        assert report.issues == []


class TestDetectFinalRefineQuality:
    @pytest.mark.asyncio
    async def test_ui_noise_residual(self) -> None:
        report = QualityReport()
        md = "## 章节\nBash 复制代码\ncode"
        await detect_final_refine_quality(report, md)
        assert any(
            i.code == "llm.final_ui_noise_residual" for i in report.issues
        )

    @pytest.mark.asyncio
    async def test_duplicate_h2_detected(self) -> None:
        report = QualityReport()
        md = (
            "## 编译方式\n文本 A\n"
            "## 其他章节\n文本\n"
            "## 编译方式\n文本 B\n"
        )
        await detect_final_refine_quality(report, md)
        dup_issues = [
            i for i in report.issues
            if i.code == "llm.final_duplicate_h2"
        ]
        assert len(dup_issues) == 1
        assert "编译方式" in dup_issues[0].metadata["duplicates"]

    @pytest.mark.asyncio
    async def test_clean_doc_no_issue(self) -> None:
        report = QualityReport()
        md = "## 章节 A\n文本\n## 章节 B\n文本\n"
        await detect_final_refine_quality(report, md)
        assert report.issues == []


class TestDetectMergerQuality:
    @pytest.mark.asyncio
    async def test_weak_overlap_triggers(self) -> None:
        report = QualityReport()
        await detect_merger_quality(
            report, "p1.jpg", "p2.jpg",
            overlap_lines=0, similarity=0.5,
        )
        assert len(report.issues) == 1
        assert report.issues[0].code == "merger.weak_overlap"

    @pytest.mark.asyncio
    async def test_strong_overlap_no_issue(self) -> None:
        report = QualityReport()
        await detect_merger_quality(
            report, "p1.jpg", "p2.jpg",
            overlap_lines=3, similarity=0.95,
        )
        assert report.issues == []

    @pytest.mark.asyncio
    async def test_no_overlap_low_similarity_no_issue(self) -> None:
        """0 重叠 + 低相似度 → 正常无关联，不报。"""
        report = QualityReport()
        await detect_merger_quality(
            report, "p1.jpg", "p2.jpg",
            overlap_lines=0, similarity=0.05,
        )
        assert report.issues == []
