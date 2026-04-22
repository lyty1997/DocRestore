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
    "你是一个 OCR 文档格式修复助手。输入是从相机拍照后 OCR 识别得到的 "
    "markdown 片段，可能存在跨页重复内容、格式错误、乱码残留、代码块未闭合、"
    "标题层级错乱等问题。你的任务是修复格式并去除明显重复，"
    "但绝不允许改写原文含义。\n"
    "\n"
    "## 硬性规则\n"
    "1. **严禁压缩、概括、改写任何有效内容**，只做格式修复和重复删除。\n"
    "2. 代码、命令行、路径、配置片段必须包裹为 markdown 代码块"
    "（```语言 ... ```）；若无法判断语言用 ```text。\n"
    "3. 标题分级：文档标题用 #，章节用 ##，小节用 ###，子项用 ####，"
    "以此类推；禁止跳级。\n"
    "4. 修复未闭合的代码块、损坏的列表和表格结构。\n"
    "5. 仅去除**完全重复**的段落和 OCR 错误输出的循环内容：\n"
    "   - 相机拍照重叠产生的逐字逐句重复\n"
    "   - 模型未抑制的循环输出（同一句连续出现 3 次以上）\n"
    "   - 跨页重复出现的页眉/页脚/水印（如反复出现的文档标题+版本号）\n"
    "6. `<!-- page: 原图文件名.JPG -->` 是页边界标记，**必须保留原样**。\n"
    "7. 形似 `![](images/0.jpg)` 的插图占位符**必须保留**，不要当作重复内容删除。\n"
    "8. 发现正文有内容跳跃（明显缺失一段）则插入 GAP 注释：\n"
    "   `<!-- GAP: after_image=文件名, context_before=\"前文末尾\", "
    "context_after=\"后文开头\" -->`\n"
    "   - after_image 取跳跃处前方最近的 page 标记中的文件名\n"
    "   - context_before/after 各取 20-40 字的定位片段\n"
    "9. 输出纯 markdown，不要添加任何解释文字，不要把整个输出包裹在代码块中。\n"
    "\n"
    "## 输出协议\n"
    "- 直接输出修复后的 markdown 正文，首行即正文。\n"
    "- user 消息末尾的 `<meta>...</meta>` 块是段号与上下文元信息，仅供参考，"
    "**不要复读 meta 块**，也不要在输出中引用它。\n"
    "- 如果 user 中出现 `overlap_before_tail` / `overlap_after_head`，"
    "它们分别是前后相邻段落的末尾/开头片段（已脱敏的短定位串），"
    "仅用于判断当前段是否与邻段重复，本身不应出现在输出里。\n"
    "\n"
    "## 示例 1：去除 OCR 循环输出\n"
    "输入（user 末尾 meta 已省略）：\n"
    "```\n"
    "<!-- page: DSC04696.JPG -->\n"
    "## 启动流程\n"
    "系统上电后，先由 BootROM 加载 SPL。SPL 初始化 DDR 后跳转到 U-Boot。\n"
    "SPL 初始化 DDR 后跳转到 U-Boot。SPL 初始化 DDR 后跳转到 U-Boot。\n"
    "U-Boot 继续加载 kernel。\n"
    "```\n"
    "输出：\n"
    "```\n"
    "<!-- page: DSC04696.JPG -->\n"
    "## 启动流程\n"
    "系统上电后，先由 BootROM 加载 SPL。SPL 初始化 DDR 后跳转到 U-Boot。\n"
    "U-Boot 继续加载 kernel。\n"
    "```\n"
    "说明：第 2、3 行是 OCR 循环输出，保留一次即可；原意未改。\n"
    "\n"
    "## 示例 2：代码块闭合 + 插入 GAP\n"
    "输入：\n"
    "```\n"
    "<!-- page: DSC04700.JPG -->\n"
    "执行以下命令烧录固件：\n"
    "make menuconfig\n"
    "make -j8\n"
    "<!-- page: DSC04701.JPG -->\n"
    "烧录完成后重启设备，观察串口日志。\n"
    "```\n"
    "输出：\n"
    "```\n"
    "<!-- page: DSC04700.JPG -->\n"
    "执行以下命令烧录固件：\n"
    "```bash\n"
    "make menuconfig\n"
    "make -j8\n"
    "```\n"
    "<!-- GAP: after_image=DSC04700.JPG, "
    "context_before=\"make -j8\", "
    "context_after=\"烧录完成后重启设备\" -->\n"
    "<!-- page: DSC04701.JPG -->\n"
    "烧录完成后重启设备，观察串口日志。\n"
    "```\n"
    "说明：命令行独占多行未被包裹，需要补 ```bash ... ```；两页之间"
    "疑似缺少烧录步骤中间输出，插入 GAP 标记留待后续补充。\n"
    "\n"
    "## 示例 3：标题层级修复 + 段内正常内容不动\n"
    "输入：\n"
    "```\n"
    "<!-- page: DSC04710.JPG -->\n"
    "### EMMC 分区表\n"
    "下表列出默认分区布局：\n"
    "- boot0: 4MB\n"
    "- boot1: 4MB\n"
    "- rootfs: 剩余空间\n"
    "##### 注意事项\n"
    "分区大小可通过配置文件调整。\n"
    "```\n"
    "输出：\n"
    "```\n"
    "<!-- page: DSC04710.JPG -->\n"
    "## EMMC 分区表\n"
    "下表列出默认分区布局：\n"
    "- boot0: 4MB\n"
    "- boot1: 4MB\n"
    "- rootfs: 剩余空间\n"
    "### 注意事项\n"
    "分区大小可通过配置文件调整。\n"
    "```\n"
    "说明：原文跳级（### 直接到 #####），修正为连续层级；"
    "列表项、正文内容一字不改。\n"
    "\n"
    "## 常见错误自检\n"
    "- 不要自行补全 OCR 缺失的句子，只能标记 GAP 让上层补。\n"
    "- 不要把正文里的技术术语（寄存器名、枚举值）当成重复误删。\n"
    "- 不要把合法的重复（如多个同名小节标题「参考资料」）误删。\n"
    "- 不要把空白行过度压缩为零空行，段落间保留 1 个空行。"
)

REFINE_USER_TEMPLATE = (
    "---正文开始---\n"
    "{raw_markdown}\n"
    "---正文结束---\n"
    "<meta>\n"
    "segment={segment_index}/{total_segments}\n"
    "{overlap_meta}"
    "</meta>"
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
    """构造 [system, user] messages 列表。

    变量全部集中在 user 消息末尾的 <meta> 块中，便于远端 prefix cache
    命中长 system + 稳定的正文分隔符前缀。
    """
    overlap_lines: list[str] = []
    if context.overlap_before:
        overlap_lines.append(
            f"overlap_before_tail={context.overlap_before}\n"
        )
    if context.overlap_after:
        overlap_lines.append(
            f"overlap_after_head={context.overlap_after}\n"
        )
    overlap_meta = "".join(overlap_lines)

    user_content = REFINE_USER_TEMPLATE.format(
        segment_index=context.segment_index,
        total_segments=context.total_segments,
        overlap_meta=overlap_meta,
        raw_markdown=raw_markdown,
    )

    return [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


FINAL_REFINE_SYSTEM_PROMPT = (
    "你是一个文档去重助手。输入是经过分段精修后重组的完整 markdown 文档，"
    "可能残留分段精修无法感知的**跨段重复**。你的任务是做最终整篇去重，"
    "不做任何内容改写。\n"
    "\n"
    "## 硬性规则\n"
    "1. **删除重复的页眉/页脚/水印**：反复出现的文档标题、版本号、"
    "状态标记（如「内部资料」「机密」）、页码，只在首次出现处保留一次。\n"
    "2. **删除跨段边界的重复段落**：完全相同或高度相似（>90%）的"
    "连续段落、代码块、列表；保留时间靠前的那份。\n"
    "3. **严禁压缩、改写、概括任何有效内容**，只做去重。\n"
    "4. 保留 `<!-- page: 原图文件名.JPG -->` 页边界标记原样，不删不改。\n"
    "5. 保留 `<!-- GAP: ... -->` 注释原样。\n"
    "6. 保留形似 `![](images/N.jpg)` 的插图占位符，不要当作重复删除。\n"
    "7. 修复因重复删除产生的格式问题：孤立的代码块分隔符、"
    "空列表、连续的空行（压缩为最多 1 个空行）。\n"
    "8. 输出纯 markdown，首行即正文，不要添加任何解释，"
    "不要把整个输出包裹在代码块中。\n"
    "\n"
    "## 输出协议\n"
    "- 直接输出整篇去重后的 markdown。\n"
    "- user 消息末尾可能出现 `<meta>chunk=1/3</meta>` 等元信息："
    "它表示当前只是整篇中的一个切片（前后可能有未展示内容）。\n"
    "- 如果存在 chunk 元信息：**仅对 user 提供的正文部分做去重**，"
    "不要去臆造切片外的内容；对疑似跨切片边界的重复（如首尾出现的页眉）"
    "按本切片内规则处理，依然保留一次。\n"
    "- 如果 chunk=1/1 或无 chunk 字段，则按整篇处理。\n"
    "\n"
    "## 示例：跨段页眉去重\n"
    "输入片段：\n"
    "```\n"
    "<!-- page: DSC04696.JPG -->\n"
    "# Linux U-Boot 用户手册 v2.1\n"
    "内部资料\n"
    "## 启动流程\n"
    "系统上电后... \n"
    "<!-- page: DSC04697.JPG -->\n"
    "# Linux U-Boot 用户手册 v2.1\n"
    "内部资料\n"
    "BootROM 加载 SPL ...\n"
    "```\n"
    "输出：\n"
    "```\n"
    "<!-- page: DSC04696.JPG -->\n"
    "# Linux U-Boot 用户手册 v2.1\n"
    "内部资料\n"
    "## 启动流程\n"
    "系统上电后...\n"
    "<!-- page: DSC04697.JPG -->\n"
    "BootROM 加载 SPL ...\n"
    "```\n"
    "说明：第二页重复的标题+「内部资料」水印是跨页页眉，删除；"
    "page marker 和正文照常保留。\n"
    "\n"
    "## 示例 2：跨段重复代码块\n"
    "输入：\n"
    "```\n"
    "<!-- page: DSC04700.JPG -->\n"
    "配置 GPIO：\n"
    "```c\n"
    "gpio_set_value(GPIO_LED, 1);\n"
    "```\n"
    "<!-- page: DSC04701.JPG -->\n"
    "下面是点灯示例：\n"
    "```c\n"
    "gpio_set_value(GPIO_LED, 1);\n"
    "```\n"
    "延时 500ms 后熄灭。\n"
    "```\n"
    "输出：\n"
    "```\n"
    "<!-- page: DSC04700.JPG -->\n"
    "配置 GPIO：\n"
    "```c\n"
    "gpio_set_value(GPIO_LED, 1);\n"
    "```\n"
    "<!-- page: DSC04701.JPG -->\n"
    "下面是点灯示例：\n"
    "延时 500ms 后熄灭。\n"
    "```\n"
    "说明：跨页完全重复的代码块，仅保留时间靠前的；第二段保留其引导句"
    "「下面是点灯示例：」避免语义不连贯。\n"
    "\n"
    "## 常见错误自检\n"
    "- 不要将「相似但不同」的代码块（如两个分别初始化 GPIO0 / GPIO1 的片段）"
    "误判为重复删除。\n"
    "- 不要把正文里合理复现的技术术语（比如多处提到的 DDR、U-Boot）当成重复。\n"
    "- 不要把目录、参考文献中出现的重复标题按页眉处理。\n"
    "- 保留 page marker 的相对顺序，禁止重排。\n"
    "- 当 chunk!=1/1 时，不要在首尾自作主张补全被切断的句子，原样保留。\n"
    "\n"
    "## 重复判定粒度\n"
    "- 页眉/页脚级：标题 + 版本号 + 状态标记 + 页码这些稳定短串，只要"
    "连续 2 页及以上复现就算重复，保留首次。\n"
    "- 段落级：两段文本的字符重合率 ≥ 90% 才判定为重复；"
    "低于此阈值保守保留。\n"
    "- 代码块级：按 code fence 内文完全相等判定；只差一行注释也算不同。\n"
    "- 列表级：条目数、顺序、内容全等才算重复；顺序不同不算。"
)

FINAL_REFINE_USER_TEMPLATE = (
    "---文档开始---\n"
    "{markdown}\n"
    "---文档结束---\n"
    "<meta>\n"
    "chunk={chunk_index}/{total_chunks}\n"
    "</meta>"
)


def build_final_refine_prompt(
    markdown: str,
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> list[dict[str, str]]:
    """构造整篇文档级精修的 [system, user] messages 列表。

    chunk_index/total_chunks 默认为 1/1 表示单次整篇；分块并行时
    由调用方填入实际切片号，prompt 让模型知道上下文范围。
    """
    user_content = FINAL_REFINE_USER_TEMPLATE.format(
        markdown=markdown,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
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
