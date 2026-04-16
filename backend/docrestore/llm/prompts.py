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

from docrestore.models import DocBoundary, Gap, RefineContext

REFINE_SYSTEM_PROMPT = (
    "你是一个文档格式修复助手。输入是 OCR 识别的 markdown，可能存在"
    "重复内容、格式错误、乱码残留。规则：\n"
    "1. **严禁压缩、概括或改写任何有效内容，只需删除明显的重复内容**。\n"
    "2. 代码必须格式化为 markdown 代码块（```语言 ... ```），"
    "包括命令行、路径、配置片段等\n"
    "3. 标题分级：文档标题用 #，章节用 ##，小节用 ### 等。\n"
    "4. 修复未闭合的代码块、损坏的列表和表格\n"
    "5. 仅去除**完全重复**的段落和OCR错误输出的循环内容"
    "（OCR 拍照重叠导致的逐字重复以及未被抑制的循环输出）\n"
    "6. <!-- page: <原图文件名> --> 是页边界标记，保留不要删除\n"
    "7. 发现内容跳跃则插入 GAP 注释，格式：\n"
    "   <!-- GAP: after_image=文件名, "
    'context_before="前文", context_after="后文" -->\n'
    "   after_image 取跳跃处前方最近的 page 标记中的文件名\n"
    "8. 输出纯 markdown，不要添加解释，不要包裹在代码块中\n"
    "9. 形似 ![](images/0.jpg) 的插图占位符请不要当作重复内容删除。"
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


FINAL_REFINE_SYSTEM_PROMPT = (
    "你是一个文档去重助手。输入是经过分段精修后重组的完整 markdown 文档，"
    "可能残留分段精修无法感知的跨段重复。规则：\n"
    "1. **删除重复的页眉/页脚/水印**"
    "（如反复出现的文档标题 + 状态标记、页码等）\n"
    "2. **删除跨段边界的重复段落**"
    "（完全相同或高度相似的连续段落/代码块）\n"
    "3. **严禁压缩、改写、概括任何有效内容**，只做去重\n"
    "4. 保留 <!-- page: ... --> 页边界标记，不要删除\n"
    "5. 保留 GAP 注释，不要删除\n"
    "6. 修复因重复删除产生的格式问题"
    "（孤立的代码块分隔符、空列表等）\n"
    "7. 形似 ![](images/0.jpg) 的插图占位符请不要当作重复内容删除\n"
    "8. 输出纯 markdown，不要添加解释，不要包裹在代码块中"
)

FINAL_REFINE_USER_TEMPLATE = (
    "请对以下完整文档做最终去重精修：\n"
    "---文档开始---\n"
    "{markdown}\n"
    "---文档结束---"
)


def build_final_refine_prompt(
    markdown: str,
) -> list[dict[str, str]]:
    """构造整篇文档级精修的 [system, user] messages 列表。"""
    user_content = FINAL_REFINE_USER_TEMPLATE.format(
        markdown=markdown,
    )
    return [
        {"role": "system", "content": FINAL_REFINE_SYSTEM_PROMPT},
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


# --- Gap 自动补充 prompt ---

GAP_FILL_SYSTEM_PROMPT = (
    "你是一个文档内容修复助手。用户提供了一段文档中检测到的内容缺口信息，"
    "以及缺口相邻页面的 OCR 原始文本。\n"
    "你的任务是从 OCR 文本中找出缺失的内容片段。规则：\n"
    "1. 分析 context_before 和 context_after，理解缺失的是什么\n"
    "2. 在 OCR 文本中寻找能衔接两段上下文的内容\n"
    "3. 只输出缺失的内容片段（纯 markdown），不要包含已有的上下文\n"
    "4. 如果找不到缺失内容，只输出三个字：无法补充\n"
    "5. 不要添加解释或注释"
)

GAP_FILL_USER_TEMPLATE = (
    "## 缺口信息\n"
    "缺口出现在 {after_image} 之后。\n\n"
    "### 缺口前的内容\n{context_before}\n\n"
    "### 缺口后的内容\n{context_after}\n\n"
    "## 相邻页面 OCR 文本\n"
    "### 当前页（{after_image}）\n{current_page_text}\n\n"
    "{next_page_section}"
    "请提取缺失的内容片段："
)

GAP_FILL_EMPTY_MARKER = "无法补充"


def build_gap_fill_prompt(
    gap: Gap,
    current_page_text: str,
    next_page_text: str | None = None,
    next_page_name: str | None = None,
) -> list[dict[str, str]]:
    """构造 gap 补充的 [system, user] messages。"""
    next_page_section = ""
    if next_page_text is not None and next_page_name is not None:
        next_page_section = (
            f"### 下一页（{next_page_name}）\n"
            f"{next_page_text}\n\n"
        )

    user_content = GAP_FILL_USER_TEMPLATE.format(
        after_image=gap.after_image,
        context_before=gap.context_before,
        context_after=gap.context_after,
        current_page_text=current_page_text,
        next_page_section=next_page_section,
    )

    return [
        {"role": "system", "content": GAP_FILL_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# --- PII 实体检测 prompt ---

PII_DETECT_SYSTEM_PROMPT = (
    "你是隐私信息识别助手。分析文本中的人名和机构/公司名称。\n"
    "规则：\n"
    "1. 只识别人名（中文或英文）和机构/公司名称\n"
    "2. 忽略方括号占位符内容（如 [手机号]、[邮箱] 等）\n"
    "3. 实体必须是文本中原样出现的子串\n"
    '4. 只输出 JSON，格式：'
    '{"person_names": [...], "org_names": [...]}\n'
    "5. 没有找到则输出空数组\n"
    "6. 不要输出任何解释文字"
)


def build_pii_detect_prompt(
    text: str,
) -> list[dict[str, str]]:
    """构造 PII 实体检测的 [system, user] messages。"""
    return [
        {"role": "system", "content": PII_DETECT_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]


# --- 文档边界检测 ---

# DOC_BOUNDARY 标记正则：匹配 JSON 格式的边界标记
_DOC_BOUNDARY_PATTERN = re.compile(
    r"<!--\s*DOC_BOUNDARY:\s*(\{[^}]+\})\s*-->"
)

# 提取首个一级标题
_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def parse_doc_boundaries(
    markdown: str,
) -> tuple[str, list[DocBoundary]]:
    """解析并移除 DOC_BOUNDARY 标记。

    容错策略：JSON 解析失败的标记直接忽略，不报错。
    返回 (清理掉 DOC_BOUNDARY 标记的 markdown, DocBoundary 列表)。
    """
    import json

    boundaries: list[DocBoundary] = []

    for match in _DOC_BOUNDARY_PATTERN.finditer(markdown):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        after_page = data.get("after_page", "")
        new_title = data.get("new_title", "")
        if after_page:
            boundaries.append(
                DocBoundary(
                    after_page=str(after_page),
                    new_title=str(new_title),
                )
            )

    cleaned = _DOC_BOUNDARY_PATTERN.sub("", markdown)
    return cleaned, boundaries


def extract_first_heading(markdown: str) -> str:
    """从 markdown 中提取第一个 # 一级标题文本。

    未找到则返回空字符串。
    """
    match = _HEADING_RE.search(markdown)
    if match:
        return match.group(1).strip()
    return ""


# --- 文档边界检测 prompt ---

DOC_BOUNDARY_DETECT_SYSTEM_PROMPT = (
    "你是文档边界识别助手。输入是合并后的完整 markdown 文本，"
    "其中包含 <!-- page: 文件名.jpg --> 标记表示页边界。\n"
    "你的任务是识别文本中是否包含**多篇完全不同的文档**。规则：\n"
    "1. 仔细分析页边界标记之间的内容变化\n"
    "2. 文档切换的典型特征：\n"
    "   - 封面/标题页突然出现（新的文档标题、版本号、作者信息）\n"
    "   - 页眉页脚格式完全改变\n"
    "   - 主题/领域完全不同（如从诊断手册切换到使用指南）\n"
    "   - 目录/章节编号重新开始\n"
    "3. **不是文档边界**的情况：\n"
    "   - 同一文档内的章节切换\n"
    "   - 附录、参考文献等\n"
    "   - 内容主题相关的不同章节\n"
    "4. 输出格式：纯 JSON 数组，每个边界一个对象\n"
    '   [{"after_page":"前文档最后一页.jpg","new_title":"新文档标题"}]\n'
    "5. 如果只有一篇文档，输出空数组 []\n"
    "6. 不要输出任何解释文字"
)


def build_doc_boundary_detect_prompt(
    merged_markdown: str,
) -> list[dict[str, str]]:
    """构造文档边界检测的 [system, user] messages。"""
    return [
        {"role": "system", "content": DOC_BOUNDARY_DETECT_SYSTEM_PROMPT},
        {"role": "user", "content": merged_markdown},
    ]
