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
class PageOCR:
    """单张照片的 OCR 结果"""

    image_path: Path  # 原始照片路径
    image_size: tuple[int, int]  # 原图尺寸 (width, height)
    raw_text: str  # OCR 原始输出（含 grounding 标签）
    cleaned_text: str = ""  # 清洗后的纯文本（cleaner 填充）
    regions: list[Region] = field(default_factory=list)
    output_dir: Path | None = None  # {output_dir}/{image_stem}_OCR/
    has_eos: bool = True  # 是否正常结束


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


@dataclass
class RefinedResult:
    """LLM 精修单段的结果"""

    markdown: str  # 精修后的 markdown
    gaps: list[Gap] = field(default_factory=list)


@dataclass
class PipelineResult:
    """Pipeline 处理的最终结果"""

    output_path: Path  # 最终 .md 文件路径
    markdown: str  # 最终 markdown 内容
    images: list[Region] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)


@dataclass
class TaskProgress:
    """任务进度"""

    stage: str  # ocr / clean / merge / refine / render
    current: int = 0
    total: int = 0
    percent: float = 0.0
    message: str = ""
