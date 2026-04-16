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

"""NoRepeatNGramLogitsProcessor 单元测试

验证：
- 构造器参数校验（ngram_size / window_size 必须为正）
- 短上下文（len(input_ids) < ngram_size）直接跳过
- 重复 ngram 的后继 token 被 ban（scores 设为 -inf）
- whitelist_token_ids 中的 token 不被 ban
- window_size 限制搜索窗口（窗口外的历史重复不参与 ban）
- 无重复时 scores 原样返回（且不 clone，零拷贝）
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import torch
else:
    torch = pytest.importorskip(
        "torch", reason="torch 未安装（仅 OCR extra 需要）",
    )

from docrestore.ocr.ngram_filter import (  # noqa: E402
    NoRepeatNGramLogitsProcessor,
)


def _make_scores(vocab_size: int = 100, value: float = 0.0) -> torch.FloatTensor:
    """构造形如 logits 的 FloatTensor。"""
    return torch.full((vocab_size,), value, dtype=torch.float32)


class TestConstructor:
    """构造器参数校验"""

    def test_rejects_zero_ngram_size(self) -> None:
        with pytest.raises(ValueError, match="ngram_size"):
            NoRepeatNGramLogitsProcessor(ngram_size=0)

    def test_rejects_negative_ngram_size(self) -> None:
        with pytest.raises(ValueError, match="ngram_size"):
            NoRepeatNGramLogitsProcessor(ngram_size=-1)

    def test_rejects_zero_window_size(self) -> None:
        with pytest.raises(ValueError, match="window_size"):
            NoRepeatNGramLogitsProcessor(
                ngram_size=3, window_size=0,
            )

    def test_defaults_whitelist_to_empty_set(self) -> None:
        p = NoRepeatNGramLogitsProcessor(ngram_size=3)
        assert p.whitelist_token_ids == set()


class TestShortContext:
    """输入长度不足 ngram_size 时直接跳过"""

    def test_empty_input_passes_through(self) -> None:
        p = NoRepeatNGramLogitsProcessor(ngram_size=3)
        scores = _make_scores()
        result = p([], scores)
        # 零拷贝：未检测到重复时同一 tensor
        assert result is scores

    def test_shorter_than_ngram_size_passes_through(self) -> None:
        p = NoRepeatNGramLogitsProcessor(ngram_size=4)
        scores = _make_scores()
        result = p([1, 2, 3], scores)
        assert result is scores


class TestBanRepeatedNgram:
    """检测重复 ngram 并 ban 其后继 token"""

    def test_bans_token_forming_repeated_bigram(self) -> None:
        """ngram_size=2：历史出现 (5, 7)，当前前缀是 (5,)，则 7 被 ban。"""
        p = NoRepeatNGramLogitsProcessor(
            ngram_size=2, window_size=100,
        )
        # 序列：[5, 7, 9, 5] — 当前末尾前缀 (5,)，
        # 历史中存在 (5, 7)，所以 token 7 应被 ban
        input_ids = [5, 7, 9, 5]
        scores = _make_scores()

        result = p(input_ids, scores)

        assert math.isinf(result[7].item())
        assert result[7].item() < 0
        # 非 7 的其他 token 保持原样
        assert result[0].item() == 0.0
        assert result[9].item() == 0.0

    def test_bans_multiple_tokens(self) -> None:
        """同一前缀曾经衔接多个不同 token → 都被 ban。"""
        p = NoRepeatNGramLogitsProcessor(
            ngram_size=2, window_size=100,
        )
        # 历史：(5, 7), (5, 8) → 当前前缀 (5,)，7 和 8 都被 ban
        input_ids = [5, 7, 3, 5, 8, 2, 5]
        scores = _make_scores()

        result = p(input_ids, scores)

        assert math.isinf(result[7].item())
        assert math.isinf(result[8].item())
        assert result[3].item() == 0.0

    def test_bans_only_tokens_after_matching_prefix(self) -> None:
        """ngram_size=3：匹配 (1, 2) 前缀才 ban。"""
        p = NoRepeatNGramLogitsProcessor(
            ngram_size=3, window_size=100,
        )
        # 历史：(1, 2, 9) → 当前末尾 [..., 1, 2]，9 应被 ban
        input_ids = [1, 2, 9, 4, 5, 1, 2]
        scores = _make_scores()

        result = p(input_ids, scores)

        assert math.isinf(result[9].item())
        # 4 不在匹配前缀之后，应保持
        assert result[4].item() == 0.0


class TestWhitelist:
    """白名单 token 不被 ban"""

    def test_whitelisted_token_preserved(self) -> None:
        p = NoRepeatNGramLogitsProcessor(
            ngram_size=2,
            window_size=100,
            whitelist_token_ids={7},
        )
        input_ids = [5, 7, 9, 5]
        scores = _make_scores()

        result = p(input_ids, scores)

        # 7 在白名单里，不应被 ban
        assert not math.isinf(result[7].item())
        assert result[7].item() == 0.0


class TestWindowSize:
    """window_size 限制搜索窗口"""

    def test_repeated_outside_window_not_banned(self) -> None:
        """重复出现在 window_size 之外时不参与 ban。"""
        p = NoRepeatNGramLogitsProcessor(
            ngram_size=2, window_size=3,
        )
        # 位置 0-1 有 (5, 7)，但 window_size=3 意味着搜索区间
        # 从 max(0, len(input)-3) = 5 开始
        # input_ids 长度 8 → 搜索 [5, 8)：索引 5-6 的 ngram
        # 索引 5 的 ngram 是 (0, 5)，索引 6 的 ngram 是 (5, end) — 当前 end 就是计算的
        # 这里的当前前缀是 (5,)，找到的 ngram 是 (5, 0)（位置 5→6），
        # 所以 0 会被 ban，但 7 不会
        input_ids = [5, 7, 0, 0, 0, 0, 5, 0]
        scores = _make_scores()

        result = p(input_ids, scores)

        # 窗口外的 7 不被 ban
        assert result[7].item() == 0.0


class TestNoBanNoClone:
    """无 ban 时 scores 原样返回（未 clone）"""

    def test_no_repetition_returns_original_tensor(self) -> None:
        p = NoRepeatNGramLogitsProcessor(
            ngram_size=3, window_size=100,
        )
        # 没有任何 ngram 重复
        input_ids = [1, 2, 3, 4, 5]
        scores = _make_scores()

        result = p(input_ids, scores)

        # 无 ban 时返回同一对象（零拷贝路径）
        assert result is scores

    def test_all_bans_whitelisted_returns_original(self) -> None:
        """候选 ban 集合被白名单完全抵消时返回原 tensor。"""
        p = NoRepeatNGramLogitsProcessor(
            ngram_size=2,
            window_size=100,
            whitelist_token_ids={7},
        )
        input_ids = [5, 7, 9, 5]
        scores = _make_scores()

        result = p(input_ids, scores)

        assert result is scores
