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
from docrestore.llm.prompts import (
    build_code_refine_prompt,
    build_code_rewrite_prompt,
)

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import SourceFile

logger = logging.getLogger(__name__)


def _line_delta(before: str, after: str) -> int:
    """rewrite 模式行数差，正数 = LLM 加了行；用于 flag/审计"""
    return after.count("\n") - before.count("\n")


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

    两种模式：
      - ``mode="refine"``（默认）：字符级修正，输出行数 == 输入行数；
        解析键 ``corrected_code``、``corrections``、``unresolved``
      - ``mode="rewrite"``：允许重排格式/合并断行/补编译必需语法元素；
        输出行数可不等于输入；解析键 ``rewritten_code``、``summary``
    """

    def __init__(self, base: BaseLLMRefiner, *, mode: str = "refine") -> None:
        self._base = base
        if mode not in ("refine", "rewrite"):
            raise ValueError(
                f"CodeLLMRefiner mode 必须是 refine|rewrite，收到 {mode!r}"
            )
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    async def refine(self, source: SourceFile) -> CodeRefineResult:
        """对单个 SourceFile 跑 LLM 修正（行为按 self._mode 切换）"""
        merged = source.merged_text
        if not merged.strip():
            return CodeRefineResult(
                refined_text=merged,
                flags=["code.refine.empty_input"],
            )

        if self._mode == "rewrite":
            messages = build_code_rewrite_prompt(
                file_path=source.path,
                language=source.language,
                merged_code=merged,
            )
        else:
            messages = build_code_refine_prompt(
                file_path=source.path,
                language=source.language,
                merged_code=merged,
            )
        kwargs = self._base._build_kwargs(messages)
        # _build_kwargs 不主动设 max_tokens，对话路径靠 provider 默认（多数
        # 4096）。代码路径输出 ≈ corrected_code(≈输入) + corrections + unresolved
        # 三段 JSON，体积比输入还大，默认值很容易把响应截断成半截 JSON →
        # 落到下面的 json_decode_error 分支。这里按 input 估算给个充裕上限。
        kwargs["max_tokens"] = self._estimate_max_tokens(merged)

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

    @staticmethod
    def _estimate_max_tokens(merged_code: str) -> int:
        """按 input 长度估算 max_tokens 上限。

        - 代码主要英文/符号，1 token ≈ 4 chars 经验值；
        - 输出含 corrected_code (≈输入) + corrections (~每条 100 chars) +
          unresolved + JSON 字段开销，整体约 input × 1.6；
        - 下限 2048（短文件别给太小，防 corrections 段挤破）；
        - 上限 16384（防超长文件把 timeout / 账单顶天）。
        """
        approx_input_tokens = max(1, len(merged_code) // 4)
        target = int(approx_input_tokens * 1.6) + 512
        return max(2048, min(target, 16384))

    def _parse_and_validate(
        self, response: Any, original: str,
    ) -> CodeRefineResult:
        """解析 LLM JSON 输出，做安全校验"""
        if not response.choices:
            return CodeRefineResult(
                refined_text=original,
                flags=["code.refine.empty_choices"],
            )

        choice = response.choices[0]
        raw = choice.message.content or ""
        finish_reason = getattr(choice, "finish_reason", None)

        # 截断优先判定：finish_reason == "length" 时 raw 必然是半截 JSON，
        # 不应该当作 json_decode_error 抹掉根因 —— 提示用户调大 max_tokens
        # 或者拆小 SourceFile。
        if finish_reason == "length":
            logger.warning(
                "CodeLLMRefiner 输出被 token 上限截断（finish_reason=length, "
                "raw_len=%d, 末尾 80 字: %r），回退原文。"
                "可能需要调大 max_tokens 或拆分 SourceFile。",
                len(raw), raw[-80:] if len(raw) > 80 else raw,
            )
            return CodeRefineResult(
                refined_text=original,
                flags=["code.refine.truncated"],
                raw_response=raw,
            )

        payload = _extract_json_payload(raw)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            logger.warning(
                "CodeLLMRefiner JSON 解析失败，回退原文 "
                "(finish_reason=%s, raw_len=%d, 解析错误=%s, 前 200 字: %s)",
                finish_reason, len(raw), exc, raw[:200],
            )
            return CodeRefineResult(
                refined_text=original,
                flags=["code.refine.json_decode_error"],
                raw_response=raw,
            )

        # rewrite 模式：键名 rewritten_code，不强制行数相等
        if self._mode == "rewrite":
            refined = data.get("rewritten_code", "")
            if not isinstance(refined, str) or not refined.strip():
                return CodeRefineResult(
                    refined_text=original,
                    flags=["code.refine.bad_payload"],
                    raw_response=raw,
                )
            summary = str(data.get("summary", ""))
            return CodeRefineResult(
                refined_text=refined,
                corrections=[],
                unresolved=[],
                flags=[
                    "code.refine.mode=rewrite",
                    f"code.refine.rewrite_summary={summary[:120]}",
                    f"code.refine.line_delta={_line_delta(original, refined):+d}",
                ],
                raw_response=raw,
            )

        # refine 模式：键名 corrected_code，行数严格守恒
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
            delta = refined_lines - original_lines
            # 末尾 60 字符通常能区分"末尾多/少 \n"vs"中间硬换行"两种偏差
            tail_in = original[-60:].replace("\n", "⏎")
            tail_out = refined[-60:].replace("\n", "⏎")
            logger.warning(
                "CodeLLMRefiner 行数变化（input=%d, output=%d, delta=%+d），"
                "回退原文。input 末尾=%r，output 末尾=%r",
                original_lines, refined_lines, delta, tail_in, tail_out,
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
