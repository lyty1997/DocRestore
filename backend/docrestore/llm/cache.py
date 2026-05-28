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

"""LLM 精修结果的磁盘缓存 — 让失败任务 resume 时跳过已完成的 LLM 调用。

设计要点
========
- **内容寻址**：key = sha256(model | api_base | prompt 字面量 | 输入文本)，
  输入内容相同就命中；配置变更（换模型、换 prompt）fingerprint 变，自然 miss。
- **只缓存成功结果**：`truncated=True` 或 refine 抛异常时不 put，下次 resume
  给它再一次机会。截断的结果若写进缓存，会让后续所有续跑永远用半截输出。
- **段级 + 整文档级两套**：prompt 不同、用途不同，各自独立命名空间。
- **文件布局**：`{output_dir}/.llm_cache/{kind}_{sha}.json`；JSON 序列化便于
  人工排查，单段一个文件避免并发写冲突。

与 OCR 缓存的对比
================
OCR 层：`{stem}_OCR/result.mmd` 存在即 load，按 image stem 粒度。
LLM 层：同一份输入 → 同一 markdown 输出（纯函数），所以用内容哈希更通用 —
即使 segment 边界变化，只要文本片段相同依旧命中。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

from docrestore.models import Gap, RefinedResult

logger = logging.getLogger(__name__)

# 段级与整文档级各自独立命名空间，避免 prompt 模板不同时误命中
_KIND_SEGMENT = "seg"
_KIND_FINAL = "final"


class LLMCache:
    """LLM 精修结果磁盘缓存。

    线程/协程安全性：pipeline 当前串行调用 refine，无并发写同一 key 的场景；
    异常仅记 warning，不阻断主流程。
    """

    def __init__(self, cache_dir: Path, *, enabled: bool = True) -> None:
        self._dir = cache_dir
        self._enabled = enabled
        if enabled:
            # 提前建目录而不是 lazy，方便权限问题早暴露
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(
                    "LLM cache 目录创建失败，降级为无缓存: %s (%s)",
                    self._dir, exc,
                )
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_segment(
        self, *, model: str, api_base: str, text: str,
    ) -> RefinedResult | None:
        """命中段级缓存返回 RefinedResult；miss 返回 None。"""
        return self._get(
            _KIND_SEGMENT, model=model, api_base=api_base, text=text,
        )

    def put_segment(
        self,
        *,
        model: str,
        api_base: str,
        text: str,
        result: RefinedResult,
    ) -> None:
        """写段级缓存。`truncated=True` 直接跳过。"""
        self._put(
            _KIND_SEGMENT,
            model=model, api_base=api_base, text=text, result=result,
        )

    def get_final(
        self, *, model: str, api_base: str, markdown: str,
    ) -> RefinedResult | None:
        """命中整文档级缓存返回 RefinedResult；miss 返回 None。"""
        return self._get(
            _KIND_FINAL, model=model, api_base=api_base, text=markdown,
        )

    def put_final(
        self,
        *,
        model: str,
        api_base: str,
        markdown: str,
        result: RefinedResult,
    ) -> None:
        """写整文档级缓存。"""
        self._put(
            _KIND_FINAL,
            model=model, api_base=api_base, text=markdown, result=result,
        )

    # ── 内部 ──────────────────────────────────────────────

    def _path(self, kind: str, key: str) -> Path:
        return self._dir / f"{kind}_{key}.json"

    def _make_key(
        self, kind: str, *, model: str, api_base: str, text: str,
    ) -> str:
        """prompt 字面量 + model + api_base + 文本 → sha256 前 32 字符。

        prompt 字面量 import 在此处以避免循环依赖，且保证 prompt 文件修改
        自动触发 fingerprint 变化（Python 启动时一次性 hash）。
        """
        # 延迟 import：prompts.py 依赖 models.py，llm/__init__.py 不想拉满
        from docrestore.llm import prompts

        if kind == _KIND_SEGMENT:
            prompt_body = (
                prompts.REFINE_SYSTEM_PROMPT
                + "\n"
                + prompts.REFINE_USER_TEMPLATE
            )
        elif kind == _KIND_FINAL:
            prompt_body = (
                prompts.FINAL_REFINE_SYSTEM_PROMPT
                + "\n"
                + prompts.FINAL_REFINE_USER_TEMPLATE
            )
        else:
            # 防御：未来新增 kind 时显式失败好排查
            raise ValueError(f"未知缓存 kind: {kind}")

        h = hashlib.sha256()
        h.update(kind.encode("utf-8"))
        h.update(b"\0")
        h.update(model.encode("utf-8"))
        h.update(b"\0")
        h.update(api_base.encode("utf-8"))
        h.update(b"\0")
        h.update(prompt_body.encode("utf-8"))
        h.update(b"\0")
        h.update(text.encode("utf-8"))
        # 32 字符足够防冲突（2^128），同时文件名不过长
        return h.hexdigest()[:32]

    def _get(
        self,
        kind: str,
        *,
        model: str,
        api_base: str,
        text: str,
    ) -> RefinedResult | None:
        if not self._enabled:
            return None
        key = self._make_key(
            kind, model=model, api_base=api_base, text=text,
        )
        path = self._path(kind, key)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("LLM 缓存读取失败（忽略）: %s (%s)", path, exc)
            return None

        try:
            gaps = [Gap(**g) for g in data.get("gaps", [])]
            return RefinedResult(
                markdown=data["markdown"],
                gaps=gaps,
                # 命中的都是 put 时未截断的（put 已过滤），这里稳妥取字段
                truncated=bool(data.get("truncated", False)),
            )
        except (KeyError, TypeError) as exc:
            logger.warning("LLM 缓存格式不合法（忽略）: %s (%s)", path, exc)
            return None

    def _put(
        self,
        kind: str,
        *,
        model: str,
        api_base: str,
        text: str,
        result: RefinedResult,
    ) -> None:
        if not self._enabled:
            return
        # 核心不变式：截断的结果永远不写 — 否则 resume 会永久沿用半截输出
        if result.truncated:
            return
        key = self._make_key(
            kind, model=model, api_base=api_base, text=text,
        )
        path = self._path(kind, key)
        payload = {
            "markdown": result.markdown,
            "gaps": [asdict(g) for g in result.gaps],
            "truncated": result.truncated,
            "input_len": len(text),
        }
        # 原子写：tmp → rename，避免中途崩溃留半截文件被下次读到
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            logger.warning("LLM 缓存写入失败（忽略）: %s (%s)", path, exc)
            # 清理可能残留的 tmp
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
