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
| `backend/docrestore/models.py` | 跨模块数据对象（dataclass） |
| `backend/docrestore/pipeline/config.py` | 配置（pydantic BaseModel，嵌套结构） |

> 数据对象全部 `@dataclass`；配置从 dataclass 迁移到 pydantic `BaseModel`，使用 `model_copy(update=...)` 合并请求级覆盖、`model_dump_json()` / `model_validate_json()` 序列化。

## 3. 数据对象（models.py）

按数据流顺序排列：OCR 产物 → 合并中间产物 → 合并结果 → LLM 分段/精修 → 脱敏记录 → 多文档边界 → 最终输出 → 进度。

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
    after_image: str                   # 缺口出现在哪张照片之后（文件名，如 page57.jpg）
    context_before: str                # 缺口前的上下文（最后几行）
    context_after: str                 # 缺口后的上下文（开头几行）
    filled: bool = False               # 是否已通过 re-OCR + LLM 自动补充
    filled_content: str = ""           # 补充的内容
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
    truncated: bool = False            # LLM 输出是否因 token 上限被截断
```

**截断检测**：Pipeline 通过两种方式检测截断：
1. LLM 返回的 `finish_reason == "length"` → 直接标记
2. 启发式行数比例：输入行数 > 20 且输出行数少于输入的 70% → 标记

### 3.9 RedactionRecord

```python
@dataclass
class RedactionRecord:
    """脱敏记录（不含原始 PII 文本）"""
    kind: str                          # "phone"|"email"|"id_card"|"bank_card"|"person_name"|"org_name"
    method: str                        # "regex" | "llm"
    placeholder: str                   # 替换占位符
    count: int                         # 替换次数
```

### 3.10 DocBoundary

```python
@dataclass(frozen=True)
class DocBoundary:
    """LLM 检测到的文档边界（多文档聚类）"""
    after_page: str                    # 前一篇文档的最后一页文件名
    new_title: str                     # 新文档的标题
```

由 `LLMRefiner.detect_doc_boundaries()` 产出；Pipeline 据此把 reassemble 后的合并 markdown 拆成多份独立文档。单文档场景下返回空列表。

### 3.11 PipelineResult

```python
@dataclass
class PipelineResult:
    """Pipeline 处理的最终结果"""
    output_path: Path                  # 最终 .md 文件路径
    markdown: str                      # 最终 markdown 内容
    images: list[Region] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)          # 流程警告信息（截断等）
    redaction_records: list[RedactionRecord] = field(default_factory=list)  # PII 脱敏统计
    doc_title: str = ""                # 文档标题（多文档场景标识，单文档为 ""）
    doc_dir: str = ""                  # 相对 task.output_dir 的子目录名（单文档为 ""）
```

多文档场景下 `Pipeline.process_many()` 返回 `list[PipelineResult]`；每个子文档的产物位于 `output_dir/{doc_dir}/` 下。

### 3.12 TaskProgress

```python
@dataclass
class TaskProgress:
    """任务进度"""
    stage: str                         # ocr / clean / merge / refine / render 等（实现按阶段上报）
    current: int = 0
    total: int = 0
    percent: float = 0.0
    message: str = ""
```

实际使用的 `stage` 取值由 Pipeline 各阶段决定，常见值：`ocr` / `clean` / `merge` / `pii_redaction` / `refine` / `gap_fill` / `final_refine` / `render`。

## 4. 配置（pipeline/config.py）

配置全部使用 pydantic `BaseModel`。字段值按模块分组，下方给出关键字段（完整字段见 `backend/docrestore/pipeline/config.py`）。

### 4.1 ColumnFilterThresholds

侧栏检测阈值独立抽出，方便根据采集设备差异调参。

```python
class ColumnFilterThresholds(BaseModel):
    # 浏览器 Chrome 区域上界（y 轴）
    chrome_y_threshold: int = 80
    min_sidebar_y_spread: int = 300
    # 左右栏候选识别
    left_candidate_max_x1: int = 100
    left_candidate_max_x2: int = 220
    right_candidate_min_x1: int = 800
    right_candidate_max_width: int = 200
    # 边界扩展
    left_boundary_padding: int = 20
    right_boundary_padding: int = 20
    left_filter_padding: int = 40
    # 分栏验证
    full_width_threshold: int = 700
    main_content_ratio_threshold: float = 0.3
    min_validation_count: int = 3
    # 正文占比
    content_min_ratio: float = 0.2
    content_max_ratio: float = 0.95
    # 归一化坐标范围上界（paddle/deepseek grounding 坐标 0..coord_range）
    coord_range: int = 999
```

### 4.2 OCRConfig

字段较多，按子功能分组：

**模型与通用预处理**
- `model: str = "paddle-ocr/ppocr-v4"` — 统一模型标识符（以 `paddle-ocr/` 开头走 PaddleOCR，以 `deepseek/` 开头走 DeepSeek-OCR-2）
- `model_path: str = "models/DeepSeek-OCR-2"` — DeepSeek 本地权重路径
- `gpu_memory_utilization`, `max_model_len`, `max_tokens`
- `base_size=1024` / `crop_size=768` / `max_crops=6` / `min_crops=2`
- `normalize_mean: tuple[float, float, float] = (0.5, 0.5, 0.5)` / `normalize_std`
- `ngram_size=20` / `ngram_window_size=90` / `ngram_whitelist_token_ids={128821, 128822}`
- `prompt: str` — OCR 提示词模板（含 `<|grounding|>`）
- `gpu_id: str | None = None` — `CUDA_VISIBLE_DEVICES`，两引擎通用；`None` 表示自动，`EngineManager.ensure()` 调 `gpu_detect.pick_best_gpu()` 按显存降序挑一张

**侧栏过滤**
- `enable_column_filter: bool = False` — 默认关闭，PaddleOCR 精度不足
- `column_filter_min_sidebar: int = 5`
- `column_filter_thresholds: ColumnFilterThresholds`

**PaddleOCR 专用**
- `paddle_python: str = ""` — PaddleOCR worker conda 环境 python 路径
- `paddle_ocr_timeout: int = 300` / `paddle_restart_interval: int = 20`
- `paddle_worker_script: str = ""` — 空串回退仓库内默认路径
- `paddle_server_url: str = ""` — 为空时 EngineManager 按 host/port/api_version 自动拼装
- `paddle_server_model_name: str = "PaddleOCR-VL-1.5-0.9B"`
- `paddle_min_image_size: int = 64`
- `paddle_server_python: str = ""` — ppocr_vlm conda 环境（启 server）
- `paddle_server_host: str = "localhost"` / `paddle_server_port: int = 8119` / `paddle_server_api_version: str = "v1"`
- `paddle_server_startup_timeout: int = 300` / `paddle_server_shutdown_timeout: float = 10.0` / `paddle_server_connect_timeout: float = 2.0` / `paddle_server_poll_interval: float = 2.0`

**子进程共用**
- `worker_terminate_timeout: float = 5.0` — SIGTERM 等待超时
- `worker_stdio_buffer_bytes: int = 16 * 1024 * 1024` — stdio 单行缓冲上限，避免大图 grounding JSON 超 asyncio 默认 64KB 触发 `LimitOverrunError`

**DeepSeek-OCR-2 专用**
- `deepseek_python: str = ""` / `deepseek_ocr_timeout: int = 600`
- `deepseek_worker_script: str = ""` — 空串回退默认路径

**辅助方法**
- `build_default_paddle_server_url() -> str` — 拼装 `http://{host}:{port}/{api_version}`

### 4.3 DedupConfig

```python
class DedupConfig(BaseModel):
    similarity_threshold: float = 0.8  # 行级模糊匹配阈值
    overlap_context_lines: int = 3     # 保留给 LLM 的重叠上下文行数
    search_ratio: float = 0.7          # 取 A 尾部 / B 头部的比例
    # 跨页频率过滤（文本级侧栏去除）
    repeated_line_threshold: float = 0.5  # 行出现页比例 ≥ 此值视为噪声
    repeated_line_min_pages: int = 4      # 总页数 < 此值跳过频率过滤
    repeated_line_min_block: int = 3      # 连续噪声行最小块大小（防误删孤立行）
```

### 4.4 LLMConfig

```python
class LLMConfig(BaseModel):
    provider: str = "cloud"            # "cloud" | "local"
    model: str = ""                    # litellm 模型名
    api_base: str = ""
    api_key: str = ""
    max_chars_per_segment: int = 8000
    segment_overlap_lines: int = 5
    max_retries: int = 2
    timeout: int = 600
    enable_final_refine: bool = True
    enable_gap_fill: bool = True
    # 截断检测启发式
    truncation_ratio_threshold: float = 0.3   # 输出 < 输入 × (1 - ratio) 视为截断
    truncation_min_input_lines: int = 20      # 输入行数 ≤ 此值不触发启发式
    # 全局 LLM API 并发上限（跨所有 pipeline 共享的 asyncio.Semaphore 名额）
    max_concurrent_requests: int = 3
    # 精修结果磁盘缓存：{output_dir}/.llm_cache/ 下按内容哈希落盘，
    # resume 任务自动跳过已精修段；只缓存非截断的成功结果。
    enable_cache: bool = True
```

### 4.5 OutputConfig

```python
class OutputConfig(BaseModel):
    image_format: str = "jpg"
    image_quality: int = 95
```

### 4.6 CustomWord

自定义敏感词条目，可选代号。

```python
class CustomWord(BaseModel):
    model_config = ConfigDict(frozen=True)  # 可 hash
    word: str
    code: str = ""                     # 为空则回退 PIIConfig.custom_words_placeholder
```

### 4.7 PIIConfig

```python
class PIIConfig(BaseModel):
    enable: bool = False
    # 结构化 PII（regex）
    redact_phone: bool = True
    redact_email: bool = True
    redact_id_card: bool = True
    redact_bank_card: bool = True
    # 实体 PII（LLM 检测）
    redact_person_name: bool = True
    redact_org_name: bool = True
    # 占位符
    phone_placeholder: str = "[手机号]"
    email_placeholder: str = "[邮箱]"
    id_card_placeholder: str = "[身份证号]"
    bank_card_placeholder: str = "[银行卡号]"
    person_name_placeholder: str = "[人名]"
    org_name_placeholder: str = "[机构名]"
    # 自定义敏感词（每项可选代号）
    custom_sensitive_words: list[CustomWord] = []
    custom_words_placeholder: str = "[敏感词]"
    # 安全策略
    block_cloud_on_detect_failure: bool = True
```

### 4.8 PipelineConfig（总配置）

```python
class PipelineConfig(BaseModel):
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    pii: PIIConfig = Field(default_factory=PIIConfig)
    db_path: str = "data/docrestore.db"    # SQLite 持久化路径
    debug: bool = True                     # 落盘中间产物到 output_dir/debug/
```

> 任务并发上限从 `QueueConfig.max_concurrent_pipelines` 迁移为
> `LLMConfig.max_concurrent_requests`（语义更精确：限制的是 LLM API 调用并发，
> OCR 仍由 `scheduler.gpu_lock` 强制串行）。

## 5. 被谁依赖

| 消费方 | 使用的类型 |
|---|---|
| `ocr/` | `OCRConfig`, `PageOCR`, `Region` |
| `processing/cleaner.py` | `PageOCR` |
| `processing/dedup.py` | `PageOCR`, `MergeResult`, `MergedDocument`, `DedupConfig` |
| `processing/segmenter.py` | `Segment` |
| `llm/cloud.py` | `LLMConfig`, `RefineContext`, `RefinedResult`, `Gap` |
| `llm/local.py` | `LLMConfig`, `RefineContext`, `RefinedResult`, `Gap` |
| `privacy/` | `PIIConfig`, `RedactionRecord` |
| `output/renderer.py` | `MergedDocument`, `Region`, `OutputConfig` |
| `pipeline/` | `PipelineConfig`, `PipelineResult`, `TaskProgress`, 全部数据对象 |
| `pipeline/scheduler.py` | `LLMConfig.max_concurrent_requests` |
| `api/` | `TaskProgress`, `PipelineResult` |
