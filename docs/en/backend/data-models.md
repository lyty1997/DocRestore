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

# Data Objects and Configuration (models.py + pipeline/config.py)

## 1. Responsibilities

Defines all data structures and configuration items passed between modules, serving as the "common language" of the entire project. All other modules depend on this module; this module does not depend on any other module.

## 2. File List

| File | Responsibility |
|------|---------------|
| `backend/docrestore/models.py` | Cross-module data objects (dataclass) |
| `backend/docrestore/pipeline/config.py` | Configuration (pydantic BaseModel, nested structure) |

> All data objects use `@dataclass`. Configuration has been migrated from dataclass to pydantic `BaseModel`, using `model_copy(update=...)` to merge request-level overrides and `model_dump_json()` / `model_validate_json()` for serialization.

## 3. Data Objects (models.py)

Listed in data-flow order: OCR output -> merge intermediates -> merge result -> LLM segmentation/refinement -> redaction records -> multi-document boundaries -> final output -> progress.

### 3.1 Region

```python
@dataclass
class Region:
    """图片中检测到的区域（插图/截图）"""
    bbox: tuple[int, int, int, int]   # 像素坐标 (x1, y1, x2, y2)
    label: str                         # 区域描述
    cropped_path: Path | None = None   # 裁剪后保存路径（OCR 阶段填充）
```

### 3.2 TextLine

```python
@dataclass
class TextLine:
    """Single recognized line (OCR engine-provided line-level bbox + text)"""
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) pixel coords
    text: str
    score: float
```

**Purpose**: The abstract artifact consumed by code mode, decoupled from any specific OCR provider. Any engine plugs into the IDE layout analysis chain by populating `PageOCR.text_lines` (see `processing/ide_layout.py`, `processing/code_assembly.py`).

### 3.3 PageOCR

```python
@dataclass
class PageOCR:
    """OCR result for a single photograph"""
    image_path: Path                   # Original image path
    image_size: tuple[int, int]        # Original image size (width, height)
    raw_text: str                      # Raw OCR output (with grounding tags)
    cleaned_text: str = ""             # Cleaned plain text (populated by the cleaner)
    regions: list[Region] = field(default_factory=list)
    output_dir: Path | None = None     # Per-page output dir: {output_dir}/{image_stem}_OCR/ (OCR layer guarantees this; downstream may assert non-None)
    has_eos: bool = True               # Whether the output terminated normally (no eos may indicate a truncated repeating loop)
    text_lines: list[TextLine] = field(default_factory=list)  # Line-level bbox + text + score; required by code mode, may be empty in doc mode
```

**Lifecycle**:
- Created by the OCR layer, which populates `image_path`, `image_size`, `raw_text`, `regions`, `output_dir`, `has_eos`, and (for code mode) `text_lines`
- The cleaning layer populates `cleaned_text`
- The dedup layer reads `cleaned_text` for merging
- Code mode consumes `text_lines` for the IDE layout chain; an OCR engine without line-level output should report a capability error rather than silently degrade

### 3.4 MergeResult

```python
@dataclass
class MergeResult:
    """两页合并的中间结果"""
    text: str                          # 合并后的文本
    overlap_lines: int                 # 检测到的重叠行数
    similarity: float                  # 重叠区域的匹配相似度
```

### 3.5 Gap

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

### 3.6 MergedDocument

```python
@dataclass
class MergedDocument:
    """合并后的完整文档"""
    markdown: str                      # 合并去重后的 markdown
    images: list[Region] = field(default_factory=list)   # 所有插图
    gaps: list[Gap] = field(default_factory=list)         # 检测到的内容缺口
```

### 3.7 Segment

```python
@dataclass
class Segment:
    """文档分段（供 LLM 逐段精修）"""
    text: str                          # 分段文本
    start_line: int                    # 在原文中的起始行号
    end_line: int                      # 在原文中的结束行号
```

### 3.8 RefineContext

```python
@dataclass
class RefineContext:
    """LLM 精修上下文"""
    segment_index: int                 # 当前段序号（从 1 开始）
    total_segments: int                # 总段数
    overlap_before: str                # 与前段重叠的上下文（空字符串表示第一段）
    overlap_after: str                 # 与后段重叠的上下文（空字符串表示最后一段）
```

### 3.9 RefinedResult

```python
@dataclass
class RefinedResult:
    """LLM 精修单段的结果"""
    markdown: str                      # 精修后的 markdown
    gaps: list[Gap] = field(default_factory=list)
    truncated: bool = False            # LLM 输出是否因 token 上限被截断
```

**Truncation detection**: The Pipeline detects truncation in two ways:
1. The LLM returns `finish_reason == "length"` -- flagged directly
2. Heuristic line-count ratio: if input lines > 20 and output lines are less than 70% of input -- flagged

### 3.10 RedactionRecord

```python
@dataclass
class RedactionRecord:
    """脱敏记录（不含原始 PII 文本）"""
    kind: str                          # "phone"|"email"|"id_card"|"bank_card"|"person_name"|"org_name"
    method: str                        # "regex" | "llm"
    placeholder: str                   # 替换占位符
    count: int                         # 替换次数
```

### 3.11 DocBoundary

```python
@dataclass(frozen=True)
class DocBoundary:
    """LLM 检测到的文档边界（多文档聚类）"""
    after_page: str                    # 前一篇文档的最后一页文件名
    new_title: str                     # 新文档的标题
```

Produced by `LLMRefiner.detect_doc_boundaries()`; the Pipeline uses these to split the reassembled merged markdown into multiple independent documents. Returns an empty list in single-document scenarios.

### 3.12 PipelineResult

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

In multi-document scenarios, `Pipeline.process_many()` returns `list[PipelineResult]`; each sub-document's output is located under `output_dir/{doc_dir}/`.

### 3.13 TaskProgress

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

The actual `stage` values are determined by each Pipeline phase. Common values: `ocr` / `clean` / `merge` / `pii_redaction` / `refine` / `gap_fill` / `final_refine` / `render`; code mode additionally emits `code_layout` / `code_group` / `code_refine` / `code_render`.

### 3.14 PathCandidate / IDEMeta (Code Mode)

`processing/ide_meta_extract.py` parses each column's file-path candidates from the IDE title bar / tab / breadcrumb. A single image may yield multiple candidates (different sources, different confidences); Pipeline selects the highest-confidence one as the final path.

```python
@dataclass(frozen=True)
class PathCandidate:
    path: str | None
    filename: str | None
    language: str | None
    source: Literal["breadcrumb", "tab", "peer", "reference", "content"]
    confidence: float                  # 0.0 ~ 1.0
    raw_text: str = ""
    flags: list[str] = field(default_factory=list)

@dataclass
class IDEMeta:
    column_index: int
    filename: str | None = None        # e.g. widget_status.h
    path: str | None = None            # e.g. app/core/widget/widget_status.h
    language: str | None = None
    tab_readable: bool = False         # whether the tab filename was recognized
    breadcrumb_readable: bool = False
    raw_tab_lines: list[str] = field(default_factory=list)
    raw_breadcrumb_lines: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    path_candidates: list[PathCandidate] = field(default_factory=list)
    path_confidence: float = 0.0       # confidence of the selected path
```

### 3.15 CodeColumn (Code Mode)

`processing/code_assembly.py` assembles a single image's OCR `text_lines` into code columns anchored by line numbers. `CodeLine` is one line (line number + code + indent); `CodeColumn` aggregates all lines for one column in one image.

```python
@dataclass
class CodeLine:
    line_no: int                       # Line number (OCR-read or numerically inferred)
    text: str                          # Code content without leading indent
    indent: int                        # Indent character count (derived from char_width)
    bbox: tuple[int, int, int, int] | None = None
    is_inferred_line_no: bool = False  # True = inferred from numeric sequence rather than OCR

@dataclass
class CodeColumn:
    column_index: int
    bbox: tuple[int, int, int, int]    # Column bbox in the original image coordinate system
    code_text: str                     # Full code text (with indent + newlines)
    lines: list[CodeLine]
```

### 3.16 PageColumn (Code Mode)

`processing/code_file_grouping.py` packs a `CodeColumn` (one column on one image) together with the matching `IDEMeta` (path candidates for the same column) into a `PageColumn`, the minimum unit consumed by cross-image grouping.

```python
@dataclass
class PageColumn:
    page_stem: str                     # Source image stem (no extension)
    column_index: int                  # Column index inside its source image
    meta: IDEMeta                      # File path / language candidates
    column: CodeColumn                 # Assembled code column
```

### 3.17 SourceFile (Code Mode)

`group_into_files(all_pcs)` groups `PageColumn`s across images by path / filename, keeping only the first copy on line-number overlap; unresolved gaps are marked via `flags`. `SourceFile` is the unit consumed by code-mode LLM refinement, ocr_postfix, and rendering.

```python
@dataclass
class SourceFile:
    path: str                          # canonical path (dir/filename or filename only)
    filename: str
    language: str | None
    pages: list[PageColumn]            # sources (in line-number order)
    merged_text: str                   # concatenated code (rewritten by the refine stage)
    line_count: int
    line_no_range: tuple[int, int]
    flags: list[str] = field(default_factory=list)
```

Common `flags` values: `code.group.gap_unknown` (cross-page gap unresolvable) / `code.refine.*` / `code.repair.*` / `code.postfix.*`, written by the respective stages.

## 4. Configuration (pipeline/config.py)

All configuration uses pydantic `BaseModel`. Fields are grouped by module; key fields are listed below (see `backend/docrestore/pipeline/config.py` for the complete list).

### 4.1 ColumnFilterThresholds

Sidebar detection thresholds are extracted into a separate class for easy tuning based on different capture devices.

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

Contains many fields, grouped by sub-feature:

**Model and general preprocessing**
- `model: str = "paddle-ocr/ppocr-v4"` -- unified model identifier (prefix `paddle-ocr/` routes to PaddleOCR, prefix `deepseek/` routes to DeepSeek-OCR-2)
- `model_path: str = "models/DeepSeek-OCR-2"` -- DeepSeek local model weights path
- `gpu_memory_utilization`, `max_model_len`, `max_tokens`
- `base_size=1024` / `crop_size=768` / `max_crops=6` / `min_crops=2`
- `normalize_mean: tuple[float, float, float] = (0.5, 0.5, 0.5)` / `normalize_std`
- `ngram_size=20` / `ngram_window_size=90` / `ngram_whitelist_token_ids={128821, 128822}`
- `prompt: str` -- OCR prompt template (contains `<|grounding|>`)
- `gpu_id: str | None = None` -- `CUDA_VISIBLE_DEVICES`, shared by both engines; `None` means auto — `EngineManager.ensure()` calls `gpu_detect.pick_best_gpu()` to recommend the default OCR GPU

**Sidebar filtering**
- `enable_column_filter: bool = False` -- disabled by default due to insufficient PaddleOCR precision
- `column_filter_min_sidebar: int = 5`
- `column_filter_thresholds: ColumnFilterThresholds`

**PaddleOCR specific**
- `paddle_python: str = ""` -- PaddleOCR worker conda environment python path
- `paddle_ocr_timeout: int = 300` / `paddle_restart_interval: int = 20`
- `paddle_worker_script: str = ""` -- empty string falls back to the default in-repo path
- `paddle_server_url: str = ""` -- when empty, EngineManager auto-assembles from host/port/api_version
- `paddle_server_model_name: str = "PaddleOCR-VL-1.5-0.9B"`
- `paddle_min_image_size: int = 64`
- `paddle_server_python: str = ""` -- ppocr_vlm conda environment (for starting the server)
- `paddle_server_host: str = "localhost"` / `paddle_server_port: int = 8119` / `paddle_server_api_version: str = "v1"`
- `paddle_server_startup_timeout: int = 300` / `paddle_server_shutdown_timeout: float = 10.0` / `paddle_server_connect_timeout: float = 2.0` / `paddle_server_poll_interval: float = 2.0`

**Subprocess shared**
- `worker_terminate_timeout: float = 5.0` -- SIGTERM wait timeout
- `worker_stdio_buffer_bytes: int = 16 * 1024 * 1024` -- stdio per-line buffer limit, prevents large-image grounding JSON from exceeding asyncio's default 64 KB and triggering `LimitOverrunError`

**DeepSeek-OCR-2 specific**
- `deepseek_python: str = ""` / `deepseek_ocr_timeout: int = 600`
- `deepseek_worker_script: str = ""` -- empty string falls back to the default path

**Helper methods**
- `build_default_paddle_server_url() -> str` -- assembles `http://{host}:{port}/{api_version}`

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
    # Global LLM API concurrency cap (shared asyncio.Semaphore across pipelines)
    max_concurrent_requests: int = 3
    # Disk cache for refine results: keyed by content hash under
    # {output_dir}/.llm_cache/; lets resumed tasks skip already-refined segments.
    # Only non-truncated successful results are persisted.
    enable_cache: bool = True
```

### 4.5 OutputConfig

```python
class OutputConfig(BaseModel):
    image_format: str = "jpg"
    image_quality: int = 95
```

### 4.6 CustomWord

Custom sensitive word entry with an optional code name.

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

### 4.8 CodeRestoreConfig

AGE-8 IDE code-photo → source file configuration. When `enable=True`, Pipeline auto-switches: OCR falls back to PaddleOCR `basic` pipeline (PP-OCRv5 line-level bbox), and the IDE-specific chain (line-number anchors + column code assembly) takes over.

```python
class CodeRestoreConfig(BaseModel):
    enable: bool = False
    output_files_dir: str = "files"                # output_dir/<files_dir>/<relative-path>
    file_grouping_strategy: Literal[
        "tab_breadcrumb", "content_only",
    ] = "tab_breadcrumb"                            # only tab_breadcrumb is implemented
    secondary_column_ocr: bool = False              # crop + enhance + re-OCR each code column
    secondary_column_ocr_scale: int = 2
    secondary_column_ocr_padding_px: int = 6
    secondary_column_ocr_contrast: float = 1.35
    secondary_column_ocr_sharpness: float = 1.4
    context_root: str = ""                          # optional read-only reference source root
```

Only the `tab_breadcrumb` grouping strategy is implemented; `content_only` is a reserved enum and should not be used as if implemented.

### 4.9 CodeDiagnostic

Code-mode lightweight diagnostics. Written into the `diagnostic` field of `files-index.json` and returned to the frontend through the live diagnostics API.

```python
@dataclass(frozen=True)
class CodeDiagnosticItem:
    line: int
    column: int = 0
    severity: str = "error"
    category: str = "syntax"           # syntax / semantic / dependency
    code: str = ""                     # parse_error / missing_include / ocr_noise_non_ascii ...
    message: str = ""
    source: str = ""                   # parser or tool name

@dataclass(frozen=True)
class CodeDiagnostic:
    path: str
    language: str
    status: str                        # syntax_clean / syntax_dirty / dependency_dirty / ...
    category: str
    summary: str = ""
    failing_lines: list[int] = field(default_factory=list)
    syntax_errors: int = 0
    semantic_errors: int = 0
    dependency_errors: int = 0
    items: list[CodeDiagnosticItem] = field(default_factory=list)
    tool: str = ""
    duration_ms: int = 0
```

The legacy fields `compile_status` / `compile_failing_lines` / `compile_error` are derived from `CodeDiagnostic` by `output/code_renderer.py` and kept only for historical compatibility.

### 4.10 PipelineConfig (Top-Level Configuration)

```python
class PipelineConfig(BaseModel):
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    pii: PIIConfig = Field(default_factory=PIIConfig)
    code: CodeRestoreConfig = Field(default_factory=CodeRestoreConfig)
    db_path: str = "data/docrestore.db"    # SQLite persistence path
    debug: bool = True                     # Dump intermediates to output_dir/debug/
```

> Task concurrency moved from `QueueConfig.max_concurrent_pipelines` to
> `LLMConfig.max_concurrent_requests` (more precise semantics: it caps LLM
> API concurrency; OCR remains serialized by `scheduler.gpu_lock`).

## 5. Dependencies

| Consumer | Types Used |
|----------|-----------|
| `ocr/` | `OCRConfig`, `PageOCR`, `Region` |
| `processing/cleaner.py` | `PageOCR` |
| `processing/dedup.py` | `PageOCR`, `MergeResult`, `MergedDocument`, `DedupConfig` |
| `processing/segmenter.py` | `Segment` |
| `processing/code_file_grouping.py` | `PageColumn`, `SourceFile` |
| `llm/cloud.py` | `LLMConfig`, `RefineContext`, `RefinedResult`, `Gap` |
| `llm/local.py` | `LLMConfig`, `RefineContext`, `RefinedResult`, `Gap` |
| `privacy/` | `PIIConfig`, `RedactionRecord` |
| `output/renderer.py` | `MergedDocument`, `Region`, `OutputConfig` |
| `pipeline/` | `PipelineConfig`, `PipelineResult`, `TaskProgress`, all data objects |
| `pipeline/scheduler.py` | `LLMConfig.max_concurrent_requests` |
| `api/` | `TaskProgress`, `PipelineResult` |
