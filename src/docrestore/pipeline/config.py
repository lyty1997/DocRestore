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

"""Pipeline 配置 dataclass（嵌套结构）"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OCRConfig:
    """OCR 引擎配置"""

    engine: str = "deepseek-ocr-2"
    model_path: str = "models/DeepSeek-OCR-2"
    gpu_memory_utilization: float = 0.75
    max_model_len: int = 8192
    max_tokens: int = 8192
    # 图片预处理
    base_size: int = 1024  # 全局视图尺寸
    crop_size: int = 768  # 局部 tile 尺寸
    max_crops: int = 6
    min_crops: int = 2
    # 循环抑制
    ngram_size: int = 20
    ngram_window_size: int = 90
    ngram_whitelist_token_ids: set[int] = field(
        default_factory=lambda: {128821, 128822}
    )
    # prompt
    prompt: str = (
        "<image>\nFree OCR.\n"
        "<|grounding|>Convert the document to markdown."
    )


@dataclass
class DedupConfig:
    """去重合并配置"""

    similarity_threshold: float = 0.8  # 行级模糊匹配阈值
    overlap_context_lines: int = 3  # 保留给 LLM 的重叠上下文行数
    search_ratio: float = 0.7  # 取 A 尾部和 B 头部的比例（文档照片重叠通常较大）


@dataclass
class LLMConfig:
    """LLM 精修配置"""

    model: str = ""  # litellm 模型名
    api_base: str = ""  # 自定义 API 地址，为空用默认
    api_key: str = ""  # 为空则由 litellm 从环境变量自动读取
    max_chars_per_segment: int = 18000  # 分段上限
    segment_overlap_lines: int = 5
    max_retries: int = 2
    timeout: int = 600  # 单次请求超时（秒），慢速中转站需要更大值


@dataclass
class OutputConfig:
    """输出配置"""

    image_format: str = "jpg"
    image_quality: int = 95


@dataclass
class PipelineConfig:
    """Pipeline 总配置"""

    ocr: OCRConfig = field(default_factory=OCRConfig)
    dedup: DedupConfig = field(default_factory=DedupConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    debug: bool = True  # 落盘各阶段中间结果到 output_dir/debug/
