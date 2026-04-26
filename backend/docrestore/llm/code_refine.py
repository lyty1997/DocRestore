# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IDE 代码字符级 LLM 修正（AGE-8 Phase 3.1）

把 ``code_file_grouping.SourceFile.merged_text``（含 OCR 字符级噪声）送 LLM
做白名单字符级修正。**严禁语义改动**：行数必须等于输入；变量名/函数签名/
缩进保持原样；不能补全省略代码。

输出是 ``CodeRefineResult``，含修正后的代码 + 改动 changelog + 未解决列表
（``OCR-Q`` 标记）。下游 AGE-49 做编译验证、AGE-50 前端展示 changelog。

设计要点：
  - 输入校验：merged_text 为空 → 直接返回原文
  - 输出校验：行数变化 / JSON 解析失败 / corrections 不实 → reject 回退原文
  - LLM 调用复用 ``BaseLLMRefiner._call_llm``（continue litellm + circuit breaker）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from docrestore.llm.base import BaseLLMRefiner
from docrestore.llm.prompts import build_code_refine_prompt

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import SourceFile

logger = logging.getLogger(__name__)


@dataclass
class CodeCorrection:
    """单条字符级修正（LLM 报告 changelog）"""

    line: int
    before: str
    after: str
    reason: str = ""


@dataclass
class CodeUnresolved:
    """LLM 标记的不可识别字符位置"""

    line: int
    context: str
    note: str = ""


@dataclass
class CodeRefineResult:
    """LLM 字符级修正输出"""

    refined_text: str
    corrections: list[CodeCorrection] = field(default_factory=list)
    unresolved: list[CodeUnresolved] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    raw_response: str = ""        # 原始 LLM 输出（debug 用）


class CodeLLMRefiner:
    """IDE 代码字符级 LLM 修正器

    用 ``BaseLLMRefiner._call_llm`` 走 litellm；不复用 markdown refine 的
    截断回退 / GAP 解析，逻辑独立。
    """

    def __init__(self, base: BaseLLMRefiner) -> None:
        self._base = base

    async def refine(self, source: SourceFile) -> CodeRefineResult:
        """对单个 SourceFile 跑字符级修正"""
        merged = source.merged_text
        if not merged.strip():
            return CodeRefineResult(
                refined_text=merged,
                flags=["code.refine.empty_input"],
            )

        messages = build_code_refine_prompt(
            file_path=source.path,
            language=source.language,
            merged_code=merged,
        )
        kwargs = self._base._build_kwargs(messages)

        try:
            response = await self._base._call_llm(kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CodeLLMRefiner 调用失败，回退原文: %s", exc,
            )
            return CodeRefineResult(
                refined_text=merged,
                flags=[f"code.refine.llm_error={type(exc).__name__}"],
            )

        return self._parse_and_validate(response, merged)

    def _parse_and_validate(
        self, response: Any, original: str,
    ) -> CodeRefineResult:
        """解析 LLM JSON 输出，做安全校验"""
        if not response.choices:
            return CodeRefineResult(
                refined_text=original,
                flags=["code.refine.empty_choices"],
            )

        raw = response.choices[0].message.content or ""
        payload = _extract_json_payload(raw)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning(
                "CodeLLMRefiner JSON 解析失败，回退原文（前 200 字）: %s",
                raw[:200],
            )
            return CodeRefineResult(
                refined_text=original,
                flags=["code.refine.json_decode_error"],
                raw_response=raw,
            )

        refined = data.get("corrected_code", "")
        if not isinstance(refined, str):
            return CodeRefineResult(
                refined_text=original,
                flags=["code.refine.bad_payload"],
                raw_response=raw,
            )

        # 安全校验：行数必须等于原文
        original_lines = original.count("\n") + 1
        refined_lines = refined.count("\n") + 1
        if refined_lines != original_lines:
            logger.warning(
                "CodeLLMRefiner 行数变化（input=%d, output=%d），回退原文",
                original_lines, refined_lines,
            )
            return CodeRefineResult(
                refined_text=original,
                flags=[
                    f"code.refine.line_count_mismatch="
                    f"{original_lines}vs{refined_lines}"
                ],
                raw_response=raw,
            )

        corrections = _parse_corrections(data.get("corrections"))
        unresolved = _parse_unresolved(data.get("unresolved"))

        return CodeRefineResult(
            refined_text=refined,
            corrections=corrections,
            unresolved=unresolved,
            flags=[
                f"code.refine.applied={len(corrections)}",
                f"code.refine.unresolved={len(unresolved)}",
            ],
            raw_response=raw,
        )


def _parse_corrections(raw: object) -> list[CodeCorrection]:
    """LLM 返回的 corrections 列表 → 健壮解析"""
    if not isinstance(raw, list):
        return []
    out: list[CodeCorrection] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        try:
            out.append(CodeCorrection(
                line=int(c.get("line", 0)),
                before=str(c.get("before", "")),
                after=str(c.get("after", "")),
                reason=str(c.get("reason", "")),
            ))
        except (TypeError, ValueError):
            continue
    return out


def _parse_unresolved(raw: object) -> list[CodeUnresolved]:
    """LLM 返回的 unresolved 列表 → 健壮解析"""
    if not isinstance(raw, list):
        return []
    out: list[CodeUnresolved] = []
    for u in raw:
        if not isinstance(u, dict):
            continue
        try:
            out.append(CodeUnresolved(
                line=int(u.get("line", 0)),
                context=str(u.get("context", "")),
                note=str(u.get("note", "")),
            ))
        except (TypeError, ValueError):
            continue
    return out


def _extract_json_payload(raw: str) -> str:
    r"""从 LLM 输出中剥出 JSON 文本

    与 ``cloud._extract_json_payload`` 同款：兼容 ``\`\`\`json`` 围栏 / 纯
    JSON / 前后带说明三种形态。
    """
    text = raw.strip()
    if text.startswith("```"):
        # 剥围栏
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[: -3].rstrip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text
