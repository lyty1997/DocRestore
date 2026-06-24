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
    REFINE_SYSTEM_PROMPT,
    SLIDE_REFINE_SYSTEM_PROMPT,
    build_final_refine_prompt,
    build_refine_prompt,
    parse_gaps,
)
from docrestore.models import RefineContext


class TestBuildRefinePrompt:
    """build_refine_prompt 测试"""

    def test_basic_structure(self) -> None:
        """返回 [system, user] 两条消息，段号在末尾 meta 块中"""
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
        content = msgs[1]["content"]
        assert "segment=1/3" in content
        assert "# 标题" in content
        # user 以正文开头为前缀（便于远端 prefix cache 命中）
        assert content.startswith("---正文开始---\n")
        # meta 块在末尾
        assert content.rstrip().endswith("</meta>")

    def test_with_overlap(self) -> None:
        """带 overlap 上下文，overlap 也在末尾 meta 中"""
        ctx = RefineContext(
            segment_index=2,
            total_segments=5,
            overlap_before="前段尾部内容",
            overlap_after="后段头部内容",
        )
        msgs = build_refine_prompt("正文内容", ctx)
        content = msgs[1]["content"]
        assert "overlap_before_tail=前段尾部内容" in content
        assert "overlap_after_head=后段头部内容" in content
        # 变量必须在 <meta> 块内，不能破坏前缀
        meta_start = content.rfind("<meta>")
        assert meta_start != -1
        assert "overlap_before_tail" in content[meta_start:]
        assert "overlap_after_head" in content[meta_start:]

    def test_no_overlap(self) -> None:
        """无 overlap 时 meta 中不出现 overlap 字段"""
        ctx = RefineContext(
            segment_index=1,
            total_segments=1,
            overlap_before="",
            overlap_after="",
        )
        msgs = build_refine_prompt("正文", ctx)
        content = msgs[1]["content"]
        assert "overlap_before_tail" not in content
        assert "overlap_after_head" not in content

    def test_document_mode_uses_dedup_system_prompt(self) -> None:
        """is_slide=False（默认，文档分段）→ system 用 REFINE_SYSTEM_PROMPT。"""
        ctx = RefineContext(
            segment_index=1, total_segments=1,
            overlap_before="", overlap_after="",
        )
        msgs = build_refine_prompt("正文", ctx)
        assert msgs[0]["content"] == REFINE_SYSTEM_PROMPT

    def test_slide_mode_uses_slide_system_prompt(self) -> None:
        """is_slide=True（PPT 按页）→ system 切到 SLIDE_REFINE_SYSTEM_PROMPT。

        回归（max-effort review #2）：PPT 复用文档版"跨页去重"prompt 会误删
        合理重复的幻灯片标题/页脚。slide prompt 必须与文档版不同，且不携带
        跨页去重指令。
        """
        ctx = RefineContext(
            segment_index=1, total_segments=1,
            overlap_before="", overlap_after="", is_slide=True,
        )
        msgs = build_refine_prompt("正文", ctx)
        assert msgs[0]["content"] == SLIDE_REFINE_SYSTEM_PROMPT
        assert SLIDE_REFINE_SYSTEM_PROMPT != REFINE_SYSTEM_PROMPT
        # 语义对照：文档版含"跨页"去重指令，slide 版明确"不做跨页去重"
        assert "跨页" in REFINE_SYSTEM_PROMPT
        assert "不做跨页去重" in SLIDE_REFINE_SYSTEM_PROMPT

    def test_slide_prompt_has_ui_noise_rule(self) -> None:
        """slide prompt 含页内 UI 噪音清理规则（代码截图幻灯片的 `复制代码` /
        工具栏行），使首轮即可清理（回归 review #3）。

        旧 slide prompt 无此规则，又被 UI 噪音重试提示引用"system 规则 11-13"
        （那是文档版的编号、slide 版没有），导致代码截图幻灯片噪音无人清、
        重试也无据可依。这里断言 slide prompt 自带该清理规则。
        """
        assert "复制代码" in SLIDE_REFINE_SYSTEM_PROMPT

    def test_both_prompts_have_latex_normalization_rule(self) -> None:
        """文档版与 PPT 版都含"修 OCR LaTeX 语法错误、不改数学含义"的规范化指令。

        OCR 抽取常把矩阵行分隔 `\\\\` 误识成 `\\ `、拆开 `\\operatorname{}` 内标识符、
        漏标下标——前端只负责渲染，治本靠精修 prompt 引导模型修语法但不改语义。
        """
        # 文档分段精修：新增独立"数学公式 LaTeX 规范化"小节
        assert "## 数学公式 LaTeX 规范化" in REFINE_SYSTEM_PROMPT
        assert "矩阵" in REFINE_SYSTEM_PROMPT
        assert r"\operatorname{LowerTri}" in REFINE_SYSTEM_PROMPT
        # PPT 按页精修：规则 3 由"原样保留"改为"保留含义 + 修语法"
        assert "修正 OCR 抽取造成的" in SLIDE_REFINE_SYSTEM_PROMPT
        assert "矩阵" in SLIDE_REFINE_SYSTEM_PROMPT
        # 两版都明确不得改数学含义（保真兜底）
        assert "数学含义" in REFINE_SYSTEM_PROMPT
        assert "一律不变" in SLIDE_REFINE_SYSTEM_PROMPT


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
        # user 以正文分隔符开头（稳定前缀，利于 prefix cache）
        assert msgs[1]["content"].startswith("---文档开始---\n")

    def test_contains_markdown(self) -> None:
        """user 消息中包含输入的 markdown"""
        md = "# 文档\n\n## 章节一\n内容"
        msgs = build_final_refine_prompt(md)
        assert md in msgs[1]["content"]

    def test_chunk_meta(self) -> None:
        """chunk 元信息在末尾 meta 块中"""
        msgs = build_final_refine_prompt("正文", chunk_index=2, total_chunks=3)
        content = msgs[1]["content"]
        assert "chunk=2/3" in content
        assert content.rstrip().endswith("</meta>")

    def test_chunk_default(self) -> None:
        """默认 chunk=1/1 表示整篇单次精修"""
        msgs = build_final_refine_prompt("正文")
        assert "chunk=1/1" in msgs[1]["content"]
