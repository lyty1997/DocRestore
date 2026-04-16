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

"""滑动窗口 ngram 循环抑制 logits processor

从 DeepSeek-OCR-2 提取，补全类型注解，去除 transformers 依赖。
vLLM 的 LogitsProcessor 协议只需 __call__(input_ids, scores)。
"""

from __future__ import annotations

import torch


class NoRepeatNGramLogitsProcessor:
    """在最近 window_size token 内检测重复 ngram，ban 掉会导致循环的 token。

    白名单 token（如表格标签 <td>/<td>）不受限制。
    """

    # 兜底默认，生产路径由 OCRConfig.ngram_window_size 传入（当前 90）
    DEFAULT_WINDOW_SIZE = 90

    def __init__(
        self,
        ngram_size: int,
        window_size: int = DEFAULT_WINDOW_SIZE,
        whitelist_token_ids: set[int] | None = None,
    ) -> None:
        if ngram_size <= 0:
            msg = f"ngram_size 必须为正整数，当前值: {ngram_size}"
            raise ValueError(msg)
        if window_size <= 0:
            msg = f"window_size 必须为正整数，当前值: {window_size}"
            raise ValueError(msg)
        self.ngram_size = ngram_size
        self.window_size = window_size
        self.whitelist_token_ids = whitelist_token_ids or set()

    def __call__(
        self,
        input_ids: list[int],
        scores: torch.FloatTensor,
    ) -> torch.FloatTensor:
        """扫描滑动窗口内的 ngram，ban 掉重复 token。"""
        if len(input_ids) < self.ngram_size:
            return scores

        current_prefix = tuple(
            input_ids[-(self.ngram_size - 1) :]
        )

        search_start = max(
            0, len(input_ids) - self.window_size
        )
        search_end = (
            len(input_ids) - self.ngram_size + 1
        )

        banned_tokens: set[int] = set()
        for i in range(search_start, search_end):
            ngram = tuple(
                input_ids[i : i + self.ngram_size]
            )
            if ngram[:-1] == current_prefix:
                banned_tokens.add(ngram[-1])

        banned_tokens = (
            banned_tokens - self.whitelist_token_ids
        )

        if banned_tokens:
            scores = scores.clone()
            for token in banned_tokens:
                scores[token] = -float("inf")

        return scores
