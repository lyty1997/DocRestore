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

"""LLM prompt 模板与 GAP 解析

构造精修 prompt、从 LLM 输出中提取 GAP 标记。
"""

from __future__ import annotations

import re

from docrestore.models import Gap, RefineContext

REFINE_SYSTEM_PROMPT = (
    "你是一个文档格式修复助手。输入是 OCR 识别的 markdown，可能存在"
    "重复内容、格式错误、乱码残留。规则：\n"
    "1. 只修复格式，不改变原文内容含义，不删减有效内容\n"
    "2. 代码必须格式化为 markdown 代码块（```语言 ... ```），"
    "包括命令行、路径、配置片段等\n"
    "3. 标题层级必须正确：文档标题用 #，章节用 ##，小节用 ### 等，"
    "不要跳级\n"
    "4. 修复未闭合的代码块、损坏的列表和表格\n"
    "5. 相邻页之间可能有重复内容（OCR 拍照重叠导致），"
    "自动去重，保留信息更全面更合理的部分\n"
    "6. <!-- page: <原图文件名> --> 是页边界标记，保留不要删除\n"
    "7. 发现内容跳跃则插入 GAP 注释，格式：\n"
    "   <!-- GAP: after_image=文件名, "
    'context_before="前文", context_after="后文" -->\n'
    "   after_image 取跳跃处前方最近的 page 标记中的文件名\n"
    "8. 输出纯 markdown，不要添加解释，不要包裹在代码块中"
)

REFINE_USER_TEMPLATE = (
    "请修复以下 OCR 产出的 markdown"
    "（第 {segment_index}/{total_segments} 段）：\n"
    "{overlap_before}"
    "---正文开始---\n"
    "{raw_markdown}\n"
    "---正文结束---\n"
    "{overlap_after}"
)

# GAP 标记正则：尽力匹配，容错
_GAP_PATTERN = re.compile(
    r"<!--\s*GAP:\s*"
    r"after_image\s*=\s*(?P<image>[^,\s]+)\s*,\s*"
    r'context_before\s*=\s*"(?P<before>[^"]*)"\s*,\s*'
    r'context_after\s*=\s*"(?P<after>[^"]*)"\s*'
    r"-->"
)


def build_refine_prompt(
    raw_markdown: str, context: RefineContext
) -> list[dict[str, str]]:
    """构造 [system, user] messages 列表。"""
    overlap_before = ""
    if context.overlap_before:
        overlap_before = (
            f"---前段上下文---\n{context.overlap_before}\n"
        )

    overlap_after = ""
    if context.overlap_after:
        overlap_after = (
            f"---后段上下文---\n{context.overlap_after}\n"
        )

    user_content = REFINE_USER_TEMPLATE.format(
        segment_index=context.segment_index,
        total_segments=context.total_segments,
        overlap_before=overlap_before,
        raw_markdown=raw_markdown,
        overlap_after=overlap_after,
    )

    return [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_gaps(
    refined_markdown: str,
) -> tuple[str, list[Gap]]:
    """从 LLM 输出中提取 GAP 标记并转为 Gap 对象。

    容错策略：正则尽力匹配，畸形标记忽略，不报错。
    返回 (清理掉 GAP 标记的 markdown, Gap 列表)。
    """
    gaps: list[Gap] = []

    for match in _GAP_PATTERN.finditer(refined_markdown):
        gaps.append(
            Gap(
                after_image=match.group("image"),
                context_before=match.group("before"),
                context_after=match.group("after"),
            )
        )

    cleaned = _GAP_PATTERN.sub("", refined_markdown)
    return cleaned, gaps
