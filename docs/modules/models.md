<!--
Copyright 2026 @lyty1997

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# 数据对象与配置（models.py + pipeline/config.py）

## 1. 职责

定义所有模块间传递的数据结构和配置项，是整个项目的"公共语言"。所有其他模块依赖本模块，本模块不依赖任何其他模块。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `src/docrestore/models.py` | 数据对象定义 |
| `src/docrestore/pipeline/config.py` | 配置 dataclass |

## 3. 数据对象（models.py）

按数据流顺序排列：OCR 产物 → 合并中间产物 → 合并结果 → LLM 分段/精修 → 最终输出 → 进度。

### 3.1 Region

```python
@dataclass
class Region:
    """图片中检测到的区域（插图/截图）"""
    bbox: tuple[int, int, int, int]   # 像素坐标 (x1, y1, x2, y2)
    label: str                         # 区域描述
    cropped_path: Path | None = None   # 裁剪后保存路径（OCR 阶段填充）
```

### 3.2 PageOCR

```python
@dataclass
class PageOCR:
    """单张照片的 OCR 结果"""
    image_path: Path                   # 原始照片路径
    image_size: tuple[int, int]        # 原图尺寸 (width, height)
    raw_text: str                      # OCR 原始输出（含 grounding 标签）
    cleaned_text: str = ""             # 清洗后的纯文本（cleaner 填充）
    regions: list[Region] = field(default_factory=list)
    output_dir: Path | None = None     # 该页输出目录：{output_dir}/{image_stem}_OCR/（OCR 层保证填充，下游可断言非 None）
    has_eos: bool = True               # 是否正常结束（无 eos 可能是循环输出截断）
```

**生命周期**：
- OCR 层创建，填充 `image_path`、`image_size`、`raw_text`、`regions`、`output_dir`、`has_eos`
- 清洗层填充 `cleaned_text`
- 去重层读取 `cleaned_text` 进行合并

### 3.3 MergeResult

```python
@dataclass
class MergeResult:
    """两页合并的中间结果"""
    text: str                          # 合并后的文本
    overlap_lines: int                 # 检测到的重叠行数
    similarity: float                  # 重叠区域的匹配相似度
```

### 3.4 Gap

```python
@dataclass
class Gap:
    """内容缺口"""
    after_image: str                   # 缺口出现在哪张照片之后（文件名，如 DSC04657.jpg）
    context_before: str                # 缺口前的上下文（最后几行）
    context_after: str                 # 缺口后的上下文（开头几行）
```

### 3.5 MergedDocument

```python
@dataclass
class MergedDocument:
    """合并后的完整文档"""
    markdown: str                      # 合并去重后的 markdown
    images: list[Region] = field(default_factory=list)   # 所有插图
    gaps: list[Gap] = field(default_factory=list)         # 检测到的内容缺口
```

### 3.6 Segment

```python
@dataclass
class Segment:
    """文档分段（供 LLM 逐段精修）"""
    text: str                          # 分段文本
    start_line: int                    # 在原文中的起始行号
    end_line: int                      # 在原文中的结束行号
```

### 3.7 RefineContext

```python
@dataclass
class RefineContext:
    """LLM 精修上下文"""
    segment_index: int                 # 当前段序号（从 1 开始）
    total_segments: int                # 总段数
    overlap_before: str                # 与前段重叠的上下文（空字符串表示第一段）
    overlap_after: str                 # 与后段重叠的上下文（空字符串表示最后一段）
```

### 3.8 RefinedResult

```python
@dataclass
class RefinedResult:
    """LLM 精修单段的结果"""
    markdown: str                      # 精修后的 markdown
    gaps: list[Gap] = field(default_factory=list)
```

### 3.9 PipelineResult

```python
@dataclass
class PipelineResult:
    """Pipeline 处理的最终结果"""
    output_path: Path                  # 最终 .md 文件路径
    markdown: str                      # 最终 markdown 内容
    images: list[Region]               # 所有插图
    gaps: list[Gap]                    # 检测到的内容缺口
```

### 3.10 TaskProgress

```python
@dataclass
class TaskProgress:
    """任务进度"""
    stage: str                         # ocr / clean / merge / refine / render
    current: int = 0
    total: int = 0
    percent: float = 0.0
    message: str = ""
```

## 4. 配置（pipeline/config.py）

### 4.1 OCRConfig

```python
@dataclass
class OCRConfig:
    engine: str = "deepseek-ocr-2"
    model_path: str = "deepseek-ai/DeepSeek-OCR-2"
    gpu_memory_utilization: float = 0.75
    max_model_len: int = 8192
    max_tokens: int = 8192
    # 图片预处理
    base_size: int = 1024              # 全局视图尺寸
    crop_size: int = 768               # 局部 tile 尺寸
    max_crops: int = 6
    min_crops: int = 2
    # 循环抑制
    ngram_size: int = 20
    ngram_window_size: int = 90
    ngram_whitelist_token_ids: set[int] = field(default_factory=lambda: {128821, 128822})
    # prompt
    prompt: str = "<image>\nFree OCR.\n<|grounding|>Convert the document to markdown."
```

### 4.2 DedupConfig

```python
@dataclass
class DedupConfig:
    similarity_threshold: float = 0.8  # 行级模糊匹配阈值
    overlap_context_lines: int = 3     # 保留给 LLM 的重叠上下文行数
    search_ratio: float = 0.3          # 取 A 尾部和 B 头部的比例
```

### 4.3 LLMConfig

```python
@dataclass
class LLMConfig:
    model: str = ""                        # litellm 模型名
    api_base: str = ""                     # 自定义 API 地址（中转站/本地 vLLM），为空用默认
    api_key: str = ""                      # 为空则由 litellm 从环境变量自动读取
    max_chars_per_segment: int = 12000     # 分段上限（硬编码默认值，用户可覆盖）
    segment_overlap_lines: int = 5
    max_retries: int = 2
```

### 4.4 OutputConfig

```python
@dataclass
class OutputConfig:
    image_format: str = "jpg"
    image_quality: int = 95
```

### 4.5 PipelineConfig（总配置）

```python
@dataclass
class PipelineConfig:
    ocr: OCRConfig = field(default_factory=OCRConfig)
    dedup: DedupConfig = field(default_factory=DedupConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
```

## 5. 被谁依赖

| 消费方 | 使用的类型 |
|---|---|
| `ocr/` | `OCRConfig`, `PageOCR`, `Region` |
| `processing/cleaner.py` | `PageOCR` |
| `processing/dedup.py` | `PageOCR`, `MergeResult`, `MergedDocument`, `DedupConfig` |
| `llm/segmenter.py` | `LLMConfig`, `Segment` |
| `llm/cloud.py` | `LLMConfig`, `RefineContext`, `RefinedResult`, `Gap` |
| `output/renderer.py` | `MergedDocument`, `Region`, `OutputConfig` |
| `pipeline/` | `PipelineConfig`, `PipelineResult`, `TaskProgress`, 全部数据对象 |
| `api/` | `TaskProgress`, `PipelineResult` |