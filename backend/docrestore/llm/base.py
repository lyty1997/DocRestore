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

"""LLM 精修器接口与公共实现。

- LLMRefiner：Protocol，定义精修器对外暴露的全部能力。
- BaseLLMRefiner：基于 litellm 的公共实现，云端/本地实现共享。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

import litellm

from docrestore.llm.prompts import (
    GAP_FILL_EMPTY_MARKER,
    build_doc_boundary_detect_prompt,
    build_final_refine_prompt,
    build_gap_fill_prompt,
    build_refine_prompt,
    parse_gaps,
)
from docrestore.models import DocBoundary, Gap, RefineContext, RefinedResult
from docrestore.pipeline.config import LLMConfig
from docrestore.pipeline.profiler import current_profiler

logger = logging.getLogger(__name__)
_timing_logger = logging.getLogger("docrestore.llm.timing")


def _ensure_timing_file_handler() -> None:
    """把 docrestore.llm.timing 路由到文件（幂等）。

    路径取 env DOCRESTORE_LLM_TIMING_LOG，默认 logs/llm_timing.log；
    设为空字符串可禁用。无论是否走 FastAPI create_app 都会初始化，
    覆盖 scripts/run_e2e.py 等脚本路径。
    """
    log_path = os.environ.get(
        "DOCRESTORE_LLM_TIMING_LOG", "logs/llm_timing.log",
    ).strip()
    if not log_path:
        return
    target = os.path.abspath(log_path)
    for h in _timing_logger.handlers:
        if (
            isinstance(h, logging.FileHandler)
            and os.path.abspath(h.baseFilename) == target
        ):
            return
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _timing_logger.addHandler(handler)
    _timing_logger.setLevel(logging.INFO)
    _timing_logger.propagate = False


_ensure_timing_file_handler()


class LLMRefiner(Protocol):
    """LLM 精修器接口"""

    async def refine(
        self, raw_markdown: str, context: RefineContext,
    ) -> RefinedResult:
        """精修单段：修复格式 + 检测缺口 + 还原结构，不改写内容含义。"""
        ...

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str:
        """从 re-OCR 文本中提取 gap 缺失内容。"""
        ...

    async def final_refine(
        self,
        markdown: str,
        *,
        chunk_index: int = 1,
        total_chunks: int = 1,
    ) -> RefinedResult:
        """整篇文档级精修：去除跨段重复和页眉水印。

        chunk_index/total_chunks 默认 1/1 表示单次整篇；分块并行时
        调用方填入实际切片号，模型据此判断当前是整篇还是切片。
        """
        ...

    async def detect_doc_boundaries(
        self, merged_markdown: str,
    ) -> list[DocBoundary]:
        """检测合并文本中的文档边界。"""
        ...

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """检测文本中的人名和机构名，返回 (person_names, org_names)。

        本地实现可返回 ([], [])，云端实现应调用 LLM 做实体识别。
        检测失败抛异常，由调用方决定是否阻断云端调用。
        """
        ...


class BaseLLMRefiner:
    """LLM 精修器公共实现（litellm 调用）。

    detect_pii_entities 默认返回空列表（本地 LLM 场景无需检测）；
    云端实现 CloudLLMRefiner 覆盖此方法做真实实体识别。

    semaphore 用于限制跨 pipeline 的 LLM API 全局并发。None 表示不限流，
    便于单元测试直接构造。生产路径由 Pipeline 从 Scheduler 注入。
    """

    def __init__(
        self,
        config: LLMConfig,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._config = config
        self._semaphore = semaphore

    def _compute_timeout(
        self, messages: list[dict[str, str]],
    ) -> int:
        """按 input 大小动态调单次 LLM timeout。

        公式：`base + per_1k * (chars / 1000)`，并 clamp 到 `[base, timeout_max_s]`。
        小段快速失败（避免服务端挂起拖死 subdir），大段线性放宽（LLM 本身就慢）。
        """
        msg_chars = sum(
            len(str(m.get("content", "")))
            for m in messages
        )
        adaptive = (
            self._config.timeout
            + self._config.timeout_per_1k_chars_s
            * msg_chars / 1000.0
        )
        clamped = min(
            float(self._config.timeout_max_s),
            max(float(self._config.timeout), adaptive),
        )
        return int(clamped)

    def _build_kwargs(
        self,
        messages: list[dict[str, str]],
        *,
        prediction_content: str | None = None,
    ) -> dict[str, object]:
        """构造 litellm.acompletion 公共参数。

        prediction_content 非空且 config.enable_prediction=True 时，追加
        OpenAI Predicted Outputs 参数（仅 gpt-4o 系支持，gpt-5 全系原生不支持）。

        timeout 按 input 大小动态调整（见 _compute_timeout）：短段快挂断，长段
        宽限；litellm 的 num_retries 负责重试。
        """
        kwargs: dict[str, object] = {
            "model": self._config.model,
            "messages": messages,
            "num_retries": self._config.max_retries,
            "timeout": self._compute_timeout(messages),
        }
        if self._config.api_base:
            kwargs["base_url"] = self._config.api_base
        if self._config.api_key:
            kwargs["api_key"] = self._config.api_key
        if (
            self._config.enable_prediction
            and prediction_content
        ):
            kwargs["prediction"] = {
                "type": "content",
                "content": prediction_content,
            }
        return kwargs

    @contextlib.asynccontextmanager
    async def _rate_limit(self) -> AsyncIterator[None]:
        """持有 semaphore 期间允许发起 LLM 请求。未注入 semaphore 时直接放行。"""
        if self._semaphore is None:
            yield
            return
        async with self._semaphore:
            yield

    async def _call_llm(self, kwargs: dict[str, object]) -> Any:
        """统一出口：限流 + litellm.acompletion。

        埋两个 profiler span：
        - llm.sem_wait：等待 llm_semaphore（定位并发瓶颈）
        - llm.api_call：真实 litellm 网络/重试耗时（定位上游慢）
        """
        prof = current_profiler()
        model = str(kwargs.get("model", ""))
        raw_messages = kwargs.get("messages", [])
        messages = raw_messages if isinstance(raw_messages, list) else []
        msg_chars = sum(
            len(str(m.get("content", "")))
            for m in messages
            if isinstance(m, dict)
        )

        wait_start = time.monotonic()
        async with self._rate_limit():
            wait_s = time.monotonic() - wait_start
            prof.record_external("llm.sem_wait", wait_s, model=model)
            call_start = time.monotonic()
            status = "ok"
            try:
                with prof.stage(
                    "llm.api_call", model=model, input_chars=msg_chars,
                ):
                    return await litellm.acompletion(**kwargs)
            except Exception:
                status = "error"
                raise
            finally:
                call_s = time.monotonic() - call_start
                _timing_logger.info(
                    "llm_call model=%s status=%s wait_s=%.3f call_s=%.3f "
                    "input_chars=%d",
                    model, status, wait_s, call_s, msg_chars,
                )

    async def refine(
        self, raw_markdown: str, context: RefineContext,
    ) -> RefinedResult:
        """精修单段 markdown。

        1. 构造 prompt messages
        2. litellm.acompletion 调用
        3. 解析 GAP 标记
        4. 返回 RefinedResult
        """
        messages = build_refine_prompt(raw_markdown, context)
        # 精修输出 ≈ 输入（只改格式 + 去重复），把原文作为 prediction 给支持的模型
        kwargs = self._build_kwargs(
            messages, prediction_content=raw_markdown,
        )

        response = await self._call_llm(kwargs)
        if not response.choices:
            msg = f"LLM 返回空 choices（model={self._config.model}）"
            raise RuntimeError(msg)
        choice = response.choices[0]
        content: str = choice.message.content or ""
        truncated = getattr(choice, "finish_reason", None) == "length"

        if truncated:
            logger.warning(
                "LLM 输出因 token 上限被截断（model=%s, finish_reason=length）",
                self._config.model,
            )

        cleaned_md, gaps = parse_gaps(content)
        return RefinedResult(markdown=cleaned_md, gaps=gaps, truncated=truncated)

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str:
        """从 re-OCR 文本中提取 gap 缺失内容。

        返回提取到的内容片段，空字符串表示无法填充。
        """
        messages = build_gap_fill_prompt(
            gap, current_page_text, next_page_text, next_page_name,
        )
        kwargs = self._build_kwargs(messages)

        response = await self._call_llm(kwargs)
        if not response.choices:
            msg = f"LLM 返回空 choices（model={self._config.model}）"
            raise RuntimeError(msg)

        fill_content: str = response.choices[0].message.content or ""

        if GAP_FILL_EMPTY_MARKER in fill_content.strip():
            return ""

        return fill_content.strip()

    async def final_refine(
        self,
        markdown: str,
        *,
        chunk_index: int = 1,
        total_chunks: int = 1,
    ) -> RefinedResult:
        """整篇文档级精修：去除跨段重复和页眉水印。"""
        messages = build_final_refine_prompt(
            markdown,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )
        # final_refine 同样是改写任务，输出高度相似输入 → 可用 prediction
        kwargs = self._build_kwargs(
            messages, prediction_content=markdown,
        )

        response = await self._call_llm(kwargs)
        if not response.choices:
            msg = (
                "LLM 返回空 choices"
                f"（model={self._config.model}）"
            )
            raise RuntimeError(msg)
        choice = response.choices[0]
        content: str = choice.message.content or ""
        truncated = getattr(choice, "finish_reason", None) == "length"

        if truncated:
            logger.warning(
                "LLM 整篇精修输出因 token 上限被截断"
                "（model=%s, finish_reason=length）",
                self._config.model,
            )

        cleaned_md, gaps = parse_gaps(content)
        return RefinedResult(markdown=cleaned_md, gaps=gaps, truncated=truncated)

    async def detect_doc_boundaries(
        self, merged_markdown: str,
    ) -> list[DocBoundary]:
        """检测合并文本中的文档边界。"""
        messages = build_doc_boundary_detect_prompt(merged_markdown)
        kwargs = self._build_kwargs(messages)

        response = await self._call_llm(kwargs)
        if not response.choices:
            logger.warning("文档边界检测返回空 choices，假定单文档")
            return []

        content: str = response.choices[0].message.content or "[]"
        try:
            data = json.loads(content.strip())
            if not isinstance(data, list):
                logger.warning("文档边界检测返回非数组，假定单文档")
                return []

            boundaries: list[DocBoundary] = []
            for item in data:
                if isinstance(item, dict):
                    after_page = item.get("after_page", "")
                    new_title = item.get("new_title", "")
                    if after_page:
                        boundaries.append(
                            DocBoundary(
                                after_page=str(after_page),
                                new_title=str(new_title),
                            )
                        )
            return boundaries
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("文档边界检测 JSON 解析失败: %s，假定单文档", e)
            return []

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """默认实现：不做实体检测，返回空列表。

        本地 LLM 实现继承此默认行为（数据不出本地，无需识别）。
        云端实现应覆盖此方法调用 LLM 做真实识别。
        """
        _ = text
        return [], []
