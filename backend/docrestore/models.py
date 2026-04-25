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

"""跨模块数据对象定义

按数据流顺序：OCR 产物 → 合并中间产物 → 合并结果 → LLM 分段/精修 → 最终输出 → 进度
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Region:
    """图片中检测到的区域（插图/截图）"""

    bbox: tuple[int, int, int, int]  # 像素坐标 (x1, y1, x2, y2)
    label: str  # 区域描述
    cropped_path: Path | None = None  # 裁剪后保存路径（OCR 阶段填充）


@dataclass
class TextLine:
    """单行文字识别结果（PaddleOCR basic pipeline 行级 bbox + text）

    用于 AGE-8 IDE 代码场景：从 PageOCR 的行级输出做布局聚类，
    比 layout block 级别细 6 倍以上，每个 IDE 元素（tab、行号、代码行、
    terminal 行）都是独立 TextLine。
    """

    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) 像素坐标
    text: str
    score: float


@dataclass
class PageOCR:
    """单张照片的 OCR 结果"""

    image_path: Path  # 原始照片路径
    image_size: tuple[int, int]  # 原图尺寸 (width, height)
    raw_text: str  # OCR 原始输出（含 grounding 标签）
    cleaned_text: str = ""  # 清洗后的纯文本（cleaner 填充）
    regions: list[Region] = field(default_factory=list)
    output_dir: Path | None = None  # {output_dir}/{image_stem}_OCR/
    has_eos: bool = True  # 是否正常结束
    #: 行级 bbox + text + score；basic pipeline 填充，vl 模式留空。
    #: AGE-8 IDE 代码场景必填，用于行号列锚点布局识别。
    text_lines: list[TextLine] = field(default_factory=list)


@dataclass
class MergeResult:
    """两页合并的中间结果"""

    text: str  # 合并后的文本
    overlap_lines: int  # 检测到的重叠行数
    similarity: float  # 重叠区域的匹配相似度


@dataclass
class Gap:
    """内容缺口"""

    after_image: str  # 缺口出现在哪张照片之后（文件名）
    context_before: str  # 缺口前的上下文
    context_after: str  # 缺口后的上下文
    filled: bool = False  # 是否已自动补充
    filled_content: str = ""  # 补充的内容


@dataclass
class MergedDocument:
    """合并后的完整文档"""

    markdown: str  # 合并去重后的 markdown
    images: list[Region] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)


@dataclass
class Segment:
    """文档分段（供 LLM 逐段精修）"""

    text: str  # 分段文本
    start_line: int  # 在原文中的起始行号
    end_line: int  # 在原文中的结束行号


@dataclass
class RefineContext:
    """LLM 精修上下文"""

    segment_index: int  # 当前段序号（从 1 开始）
    total_segments: int  # 总段数
    overlap_before: str  # 与前段重叠的上下文（空字符串表示第一段）
    overlap_after: str  # 与后段重叠的上下文（空字符串表示最后一段）
    #: 重试提示：A-2 选择性重跑时注入的额外指令，提醒 LLM 上一轮具体
    #: 漏掉/错做的事（如"还有 3 处 UI 噪音未清"），空=无重试提示。
    retry_hint: str = ""


@dataclass
class RefinedResult:
    """LLM 精修单段的结果"""

    markdown: str  # 精修后的 markdown
    gaps: list[Gap] = field(default_factory=list)
    truncated: bool = False  # LLM 输出是否因 token 上限被截断


@dataclass
class RedactionRecord:
    """脱敏记录（不含原始 PII 文本）"""

    # "phone"|"email"|"id_card"|"bank_card"|"person_name"|"org_name"
    kind: str
    method: str  # "regex" | "llm"
    placeholder: str  # 替换占位符
    count: int  # 替换次数


@dataclass(frozen=True)
class DocBoundary:
    """LLM 检测到的文档边界"""

    after_page: str  # 前一篇文档的最后一页文件名
    new_title: str  # 新文档的标题


@dataclass
class PipelineResult:
    """Pipeline 处理的最终结果"""

    output_path: Path  # 最终 .md 文件路径
    markdown: str  # 最终 markdown 内容
    images: list[Region] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # 流程警告信息
    redaction_records: list[RedactionRecord] = field(
        default_factory=list,
    )
    doc_title: str = ""  # 文档标题（多文档标识）
    doc_dir: str = ""  # 相对于 task.output_dir 的子目录名（空=根目录）
    # 子文档级错误：process_tree 某个 leaf 失败时用此字段占位；空串代表成功。
    # 允许前端按 doc 粒度展示"部分完成"状态：成功 doc 可正常预览，失败 doc
    # 只展示 error 文本 + 保留 doc_dir 供 resume 跳过。
    error: str = ""


@dataclass
class TaskProgress:
    """任务进度"""

    stage: str  # ocr / clean / merge / refine / render
    current: int = 0
    total: int = 0
    percent: float = 0.0
    #: 人类可读文本，服务端默认用简体中文拼，保留给 CLI / 日志 / 老客户端 fallback。
    message: str = ""
    # 并行子目录标识：非空表示这是某个子目录的进度帧（见 process_tree）；
    # 空表示任务级/单目录主进度。前端按该字段分轨渲染。
    subtask: str = ""
    #: 结构化文案 key（i18n 入口）：前端按当前语言渲染，避免服务端写死语言。
    #: 典型值见 pipeline.py 各 report_fn 调用点；空串表示本帧无结构化文案，
    #: 前端 fallback 到 `message` 原文。
    message_key: str = ""
    #: 结构化文案的插值参数（值统一 str，避免 WS JSON 里混 int/float 抖动）。
    message_params: dict[str, str] = field(default_factory=dict)
