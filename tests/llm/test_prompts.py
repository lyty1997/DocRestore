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

"""prompts.py 单元测试"""

from __future__ import annotations

from docrestore.llm.prompts import (
    build_final_refine_prompt,
    build_refine_prompt,
    parse_gaps,
)
from docrestore.models import RefineContext


class TestBuildRefinePrompt:
    """build_refine_prompt 测试"""

    def test_basic_structure(self) -> None:
        """返回 [system, user] 两条消息"""
        ctx = RefineContext(
            segment_index=1,
            total_segments=3,
            overlap_before="",
            overlap_after="",
        )
        msgs = build_refine_prompt("# 标题\n正文", ctx)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "1/3" in msgs[1]["content"]
        assert "# 标题" in msgs[1]["content"]

    def test_with_overlap(self) -> None:
        """带 overlap 上下文"""
        ctx = RefineContext(
            segment_index=2,
            total_segments=5,
            overlap_before="前段尾部内容",
            overlap_after="后段头部内容",
        )
        msgs = build_refine_prompt("正文内容", ctx)
        content = msgs[1]["content"]
        assert "前段上下文" in content
        assert "前段尾部内容" in content
        assert "后段上下文" in content
        assert "后段头部内容" in content

    def test_no_overlap(self) -> None:
        """无 overlap 时不包含上下文标记"""
        ctx = RefineContext(
            segment_index=1,
            total_segments=1,
            overlap_before="",
            overlap_after="",
        )
        msgs = build_refine_prompt("正文", ctx)
        content = msgs[1]["content"]
        assert "前段上下文" not in content
        assert "后段上下文" not in content


class TestParseGaps:
    """parse_gaps 测试"""

    def test_normal_gap(self) -> None:
        """正常 GAP 标记解析"""
        md = (
            "一些文本\n"
            "<!-- GAP: after_image=page57.jpg, "
            'context_before="前文最后", '
            'context_after="后文开头" -->\n'
            "更多文本"
        )
        cleaned, gaps = parse_gaps(md)
        assert len(gaps) == 1
        assert gaps[0].after_image == "page57.jpg"
        assert gaps[0].context_before == "前文最后"
        assert gaps[0].context_after == "后文开头"
        # GAP 标记已从 markdown 中移除
        assert "GAP" not in cleaned
        assert "一些文本" in cleaned
        assert "更多文本" in cleaned

    def test_multiple_gaps(self) -> None:
        """多个 GAP 标记"""
        md = (
            '<!-- GAP: after_image=A.jpg, context_before="a", '
            'context_after="b" -->\n'
            "中间文本\n"
            '<!-- GAP: after_image=B.jpg, context_before="c", '
            'context_after="d" -->'
        )
        cleaned, gaps = parse_gaps(md)
        assert len(gaps) == 2
        assert gaps[0].after_image == "A.jpg"
        assert gaps[1].after_image == "B.jpg"

    def test_malformed_gap_ignored(self) -> None:
        """畸形 GAP 标记被忽略，且非 GAP 正文一字不改。"""
        md = (
            "正常文本\n"
            "<!-- GAP: 缺少字段 -->\n"
            "<!-- GAP: after_image= -->\n"
            "更多文本"
        )
        cleaned, gaps = parse_gaps(md)
        assert len(gaps) == 0
        # 畸形标记不匹配，整段 markdown 原样保留（不得静默丢内容）
        assert cleaned == md

    def test_no_gaps(self) -> None:
        """无 GAP 标记返回空列表"""
        md = "# 标题\n\n正文内容，没有任何 GAP。"
        cleaned, gaps = parse_gaps(md)
        assert len(gaps) == 0
        assert cleaned == md


class TestBuildFinalRefinePrompt:
    """build_final_refine_prompt 测试"""

    def test_basic_structure(self) -> None:
        """返回 [system, user] 两条消息，且 user 内嵌入了输入。"""
        md = "# 标题\n正文内容"
        msgs = build_final_refine_prompt(md)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        # 输入必须完整出现在 user（不能被截断或剪裁）
        assert md in msgs[1]["content"]

    def test_contains_markdown(self) -> None:
        """user 消息中包含输入的 markdown"""
        md = "# 文档\n\n## 章节一\n内容"
        msgs = build_final_refine_prompt(md)
        assert md in msgs[1]["content"]

    def test_system_prompt_keywords(self) -> None:
        """system prompt 包含去重相关关键指令"""
        msgs = build_final_refine_prompt("正文")
        system = msgs[0]["content"]
        assert "页眉" in system
        assert "重复" in system
        assert "严禁压缩" in system

    def test_preserves_page_markers(self) -> None:
        """system prompt 要求保留页边界标记"""
        msgs = build_final_refine_prompt("正文")
        system = msgs[0]["content"]
        assert "<!-- page:" in system
