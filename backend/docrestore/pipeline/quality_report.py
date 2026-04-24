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

"""质量检测器：收集每阶段的异常信号，产出 `.quality_report.json`。

设计原则：
- **只收集，不重做**：A-1 阶段只把怀疑项记录下来。重做由 A-2 消费本模块的
  事件触发。
- **非阻塞**：所有检测都是只读 + 简单正则/统计，不引入 LLM 调用。
- **稳定的 code**：每个 issue 带 code 字段（如 `cleaner.high_garbage_ratio`），
  供 UI / 下游 A-2 路由 / 用户筛选。

用法：
    report = QualityReport()
    await report.add(QualityIssue(stage="cleaner", code="cleaner.high_garbage_ratio",
                                  page="DSC04725.JPG", severity="warn", ...))
    await report.dump_to_file(output_dir / ".quality_report.json")
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles

logger = logging.getLogger(__name__)


@dataclass
class QualityIssue:
    """单条质量异常记录。"""

    stage: str
    """阶段名：`cleaner` / `merger` / `llm_segment` / `llm_final_refine`。"""

    code: str
    """稳定标识符，用于 UI 路由、A-2 重做策略、指标统计。"""

    severity: str = "warn"
    """`info` / `warn` / `error`。决定前端是否弹警示。"""

    message: str = ""
    """人类可读描述（中文）。"""

    page: str = ""
    """关联图片文件名（如 `DSC04725.JPG`），无关联时空。"""

    segment_index: int = 0
    """关联段号，无关联时 0。段号从 1 起计。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """额外结构化数据：统计值 / 阈值 / 触发上下文。"""

    timestamp: str = ""
    """ISO 8601 UTC 时间戳，由 add() 自动填充。"""


class QualityReport:
    """任务级质量报告收集器（线程/协程安全）。"""

    def __init__(self) -> None:
        self._issues: list[QualityIssue] = []
        self._lock = asyncio.Lock()

    async def add(self, issue: QualityIssue) -> None:
        """追加一条 issue；自动填充 timestamp。

        并发调用安全（内部 asyncio.Lock）。
        """
        if not issue.timestamp:
            issue.timestamp = datetime.now(UTC).isoformat()
        async with self._lock:
            self._issues.append(issue)
        logger.debug(
            "quality issue: stage=%s code=%s page=%s seg=%d sev=%s",
            issue.stage, issue.code, issue.page,
            issue.segment_index, issue.severity,
        )

    @property
    def issues(self) -> list[QualityIssue]:
        """返回内部列表的浅拷贝（诊断用，避免外部乱改）。"""
        return list(self._issues)

    def summary(self) -> dict[str, Any]:
        """聚合统计：总数 + 按 stage / severity 分类计数。"""
        by_stage = Counter(i.stage for i in self._issues)
        by_severity = Counter(i.severity for i in self._issues)
        by_code = Counter(i.code for i in self._issues)
        return {
            "total": len(self._issues),
            "by_stage": dict(by_stage),
            "by_severity": dict(by_severity),
            "by_code": dict(by_code),
        }

    def to_dict(self) -> dict[str, Any]:
        """完整 JSON 可序列化字典。"""
        return {
            "summary": self.summary(),
            "issues": [asdict(i) for i in self._issues],
        }

    async def dump_to_file(self, path: Path) -> None:
        """写入 JSON 文件（异步）。父目录不存在则创建。"""
        await asyncio.to_thread(
            path.parent.mkdir, parents=True, exist_ok=True,
        )
        content = json.dumps(
            self.to_dict(), ensure_ascii=False, indent=2,
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)
        logger.info(
            "质量报告写入: %s (共 %d 条 issue)",
            path, len(self._issues),
        )


# --- 具体检测器：无状态函数，直接调用写入 report ---

#: UI 噪音残留检测正则：final_refine 后仍出现的视觉 UI 元素。
#: 与 cleaner.py 的 _UI_NOISE_LINE_RE 保持一致。
#: 导出给 A-2 selective re-run 模块判定重试条件。
UI_NOISE_RESIDUAL_RE = _UI_NOISE_RESIDUAL_RE = re.compile(
    r"(?:Plain\s+Text|Bash|Shell|Python|Java|JavaScript|TypeScript|"
    r"C\+\+|C#|Go|Rust|Ruby|PHP|SQL|JSON|YAML|XML|HTML|CSS|Markdown|"
    r"Dockerfile|Makefile|Kotlin|Swift|C)\s*复制代码",
    re.IGNORECASE,
)

#: 一级/二级标题匹配：用于检测 final_refine 后重复 H2。
#: 导出给 A-2 selective re-run 模块判定重试条件。
H2_LINE_RE = _H2_LINE_RE = re.compile(
    r"^##\s+(.+)$", re.MULTILINE,
)


def find_duplicate_h2_titles(markdown: str) -> list[str]:
    """返回出现 ≥ 2 次的 H2 标题列表（按字母序）。

    A-2 信号 4 / final_refine 质量检测共用。
    """
    titles = [
        m.group(1).strip()
        for m in H2_LINE_RE.finditer(markdown)
    ]
    return sorted({t for t, c in Counter(titles).items() if c >= 2})


async def detect_cleaner_quality(
    report: QualityReport,
    page_name: str,
    raw_text: str,
    cleaned_text: str,
    *,
    garbage_ratio_threshold: float = 0.15,
    min_content_chars: int = 30,
) -> None:
    """cleaner 阶段：检测被大量删除（OCR 整页烂）或清洗后近乎空。

    - `cleaner.high_garbage_ratio`：raw 被删除 ≥ 15% 字符
    - `cleaner.near_empty`：cleaned 少于 30 字符
    """
    raw_len = len(raw_text)
    cleaned_len = len(cleaned_text)

    if cleaned_len < min_content_chars and raw_len >= min_content_chars:
        await report.add(QualityIssue(
            stage="cleaner",
            code="cleaner.near_empty",
            severity="warn",
            message=(
                f"清洗后文本近乎为空（{cleaned_len} 字），"
                f"疑似 OCR 整页识别失败"
            ),
            page=page_name,
            metadata={"raw_chars": raw_len, "cleaned_chars": cleaned_len},
        ))
        return

    if raw_len > 0:
        removed_ratio = (raw_len - cleaned_len) / raw_len
        if removed_ratio >= garbage_ratio_threshold:
            await report.add(QualityIssue(
                stage="cleaner",
                code="cleaner.high_garbage_ratio",
                severity="warn",
                message=(
                    f"清洗阶段移除比例 {removed_ratio:.0%} ≥ "
                    f"{garbage_ratio_threshold:.0%}，疑似 OCR 质量差"
                ),
                page=page_name,
                metadata={
                    "raw_chars": raw_len,
                    "cleaned_chars": cleaned_len,
                    "removed_ratio": round(removed_ratio, 3),
                    "threshold": garbage_ratio_threshold,
                },
            ))


async def detect_merger_quality(
    report: QualityReport,
    prev_page_name: str,
    curr_page_name: str,
    overlap_lines: int,
    similarity: float,
) -> None:
    """merger 阶段：相邻页合并后的重叠质量。

    - `merger.weak_overlap`：similarity 在 0.3-0.8 之间（疑似漏合/误合边界）
    """
    if overlap_lines == 0 and 0.3 <= similarity < 0.8:
        await report.add(QualityIssue(
            stage="merger",
            code="merger.weak_overlap",
            severity="info",
            message=(
                f"页面 {prev_page_name} → {curr_page_name} 相似度 "
                f"{similarity:.2f} 但未合并，可能跨页重复未清"
            ),
            page=curr_page_name,
            metadata={
                "prev_page": prev_page_name,
                "similarity": round(similarity, 3),
                "overlap_lines": overlap_lines,
            },
        ))


async def detect_llm_segment_quality(
    report: QualityReport,
    segment_index: int,
    *,
    truncated: bool,
    fallback_to_raw: bool,
    output_markdown: str,
) -> None:
    """LLM 段级精修：截断 / 降级回退 / UI 噪音残留。"""
    if fallback_to_raw:
        await report.add(QualityIssue(
            stage="llm_segment",
            code="llm.seg_fallback_to_raw",
            severity="warn",
            message=f"段 {segment_index} LLM 调用失败，回退到原文",
            segment_index=segment_index,
        ))
        return

    if truncated:
        await report.add(QualityIssue(
            stage="llm_segment",
            code="llm.seg_truncated",
            severity="warn",
            message=f"段 {segment_index} LLM 输出因 token 上限截断",
            segment_index=segment_index,
        ))

    residual_hits = _UI_NOISE_RESIDUAL_RE.findall(output_markdown)
    if residual_hits:
        await report.add(QualityIssue(
            stage="llm_segment",
            code="llm.seg_ui_noise_residual",
            severity="info",
            message=(
                f"段 {segment_index} 输出仍含 UI 噪音 "
                f"{len(residual_hits)} 处（示例：{residual_hits[0]!r}）"
            ),
            segment_index=segment_index,
            metadata={"count": len(residual_hits)},
        ))


async def detect_final_refine_quality(
    report: QualityReport,
    output_markdown: str,
) -> None:
    """final_refine 阶段：UI 噪音残留 + 重复 H2 标题。"""
    residual_hits = _UI_NOISE_RESIDUAL_RE.findall(output_markdown)
    if residual_hits:
        await report.add(QualityIssue(
            stage="llm_final_refine",
            code="llm.final_ui_noise_residual",
            severity="warn",
            message=(
                f"整篇精修后仍含 UI 噪音 {len(residual_hits)} 处，"
                "prompt 规则未命中"
            ),
            metadata={"count": len(residual_hits)},
        ))

    # H2 重复检测
    duplicates = find_duplicate_h2_titles(output_markdown)
    if duplicates:
        await report.add(QualityIssue(
            stage="llm_final_refine",
            code="llm.final_duplicate_h2",
            severity="warn",
            message=(
                f"整篇精修后存在重复 H2 标题 {len(duplicates)} 个："
                f"{', '.join(duplicates[:3])}"
                + ("..." if len(duplicates) > 3 else "")
            ),
            metadata={"duplicates": duplicates},
        ))
