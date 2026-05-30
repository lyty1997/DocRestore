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

# Pipeline 编排层（pipeline/）

## 1. 职责

Pipeline 是端到端编排层，负责把各处理模块按确定顺序串起来，并对任务生命周期、进度上报、GPU 资源进行统一管理。

核心职责：

- **文档处理流水线**：
  OCR → 清洗 → 去重合并 → PII 脱敏（可选）→ 分段精修 → 重组 → 缺口补充（可选）→ 整篇精修（可选）→ 输出
- **任务生命周期**：由 `TaskManager` 驱动 `Pipeline.process()`，维护任务状态（PENDING/PROCESSING/COMPLETED/FAILED）。
- **进度上报**：通过 `on_progress` 回调（API/WS 层转发）持续推送 `TaskProgress`。
- **并发与资源**：
  - GPU 串行：OCR 及 re-OCR 使用 `asyncio.Lock` 串行化（跨任务共享锁由 Scheduler 提供）。
  - LLM 限流：所有 LLM API 调用（refine / fill_gap / final_refine / detect_*）通过
    `scheduler.llm_semaphore`（由 `LLMConfig.max_concurrent_requests` 构造）限流，
    上限对**所有同时运行的 pipeline** 生效。详见 §9.2。

> 历史变更：早期基于坐标 / 文本特征的聚类、以及后来的 LLM 文档边界检测（`DOC_BOUNDARY`）均已移除（见 §9.4 历史说明）。现在「一个叶子目录 = 一篇文档」：`process_many()` 返回单个 `PipelineResult`，`process_tree()` 每个叶子目录一份、聚合为 `list[PipelineResult]`。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `pipeline/config.py` | `PipelineConfig` 总配置（含 `db_path`；详见 [data-models.md](data-models.md)） |
| `pipeline/pipeline.py` | `Pipeline` 核心编排器 |
| `pipeline/task_manager.py` | `TaskManager` 任务生命周期管理 |
| `pipeline/scheduler.py` | `PipelineScheduler` 全局调度器（详见 [scheduler.md](scheduler.md)） |

## 3. 对外接口

### 3.1 Pipeline（pipeline/pipeline.py）

```python
class Pipeline:
    def __init__(self, config: PipelineConfig) -> None: ...

    def set_ocr_engine(self, engine: OCREngine) -> None: ...
    def set_engine_manager(self, em: EngineManager) -> None: ...
    def set_refiner(self, refiner: LLMRefiner) -> None: ...

    async def initialize(self) -> None: ...

    async def process_tree(
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None] | None = None,
        llm: LLMConfig | None = None,
        gpu_lock: asyncio.Lock | None = None,
        pii: PIIConfig | None = None,
        ocr: OCRConfig | None = None,
        code: CodeRestoreConfig | None = None,
    ) -> list[PipelineResult]: ...

    async def process_many(
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None] | None = None,
        llm: LLMConfig | None = None,
        gpu_lock: asyncio.Lock | None = None,
        pii: PIIConfig | None = None,
        ocr: OCRConfig | None = None,
        code: CodeRestoreConfig | None = None,
        controller: RateController | None = None,
    ) -> PipelineResult: ...

    async def shutdown(self) -> None: ...
```

**调用约定**：

- 必须先 `initialize()` 再 `process_tree()` / `process_many()`；任务结束后调用 `shutdown()` 释放资源。
- `process_tree()` 是统一入口：自动识别单/多子目录结构，最终委托给 `process_many()`，返回 `list[PipelineResult]`（每个叶子目录一份，单目录长度为 1）。多子目录时先用最长目录 warmup cold start，再 `asyncio.gather` 并发剩余（详见 §9.3）。
- `process_many()` **一个目录视为一篇文档**，返回**单个** `PipelineResult`；不做 LLM 文档聚合拆分。内部以「OCR 生产者 + 流式消费者」并发执行（详见 §5）。
- `controller`：`RateController` 实例。`process_tree` 并行分支会创建一个**共享** controller 传入各 `process_many`，使冷启动采样与自适应段长在子目录间复用；为 `None` 时由 `process_many` 内部临时创建。
- `gpu_lock`：
  - 若由 `PipelineScheduler` 传入，则可实现**跨任务** OCR/re-OCR 串行；
  - 不传时 Pipeline 将创建默认锁，只能保证**单次调用内**串行。
- `llm` / `ocr` / `pii`：**完整 Config 快照**，代表本次请求的最终配置；为 `None` 时 pipeline 使用 `self.config` 中的默认值。Pipeline 内部不再做"默认 dict + override dict"式合并——这一合成动作由 API 路由层在收到请求时一次性完成。
- **EngineManager 集成**：调用 `set_engine_manager()` 后，OCR 引擎延迟初始化——首次 OCR 时由 EngineManager.ensure() 按需创建。`set_ocr_engine()` 仍可用于测试注入。

### 3.2 TaskManager（pipeline/task_manager.py）

```python
@dataclass
class Task:
    task_id: str
    status: TaskStatus  # PENDING / PROCESSING / COMPLETED / FAILED
    image_dir: str
    output_dir: str
    llm: LLMConfig | None = None       # 完整快照，None 即用默认
    ocr: OCRConfig | None = None
    pii: PIIConfig | None = None
    progress: TaskProgress | None = None
    results: list[PipelineResult] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

class TaskManager:
    def __init__(
        self,
        pipeline: Pipeline,
        scheduler: PipelineScheduler | None = None,
        db: TaskDatabase | None = None,
    ) -> None: ...

    @property
    def pipeline(self) -> Pipeline: ...  # 供 API 层读取默认 Config 合成请求快照

    def create_task(
        self,
        image_dir: str,
        output_dir: str | None = None,
        llm: LLMConfig | None = None,
        ocr: OCRConfig | None = None,
        pii: PIIConfig | None = None,
    ) -> Task: ...

    async def run_task(self, task_id: str) -> None: ...

    def get_task(self, task_id: str) -> Task | None: ...

    async def subscribe_progress(self, task_id: str) -> asyncio.Queue[TaskProgress] | None: ...
    async def unsubscribe_progress(self, task_id: str, q: asyncio.Queue) -> None: ...

    def publish_progress(self, task_id: str, progress: TaskProgress) -> None: ...
```

**关键行为**：

- **无父子任务**：每个 `Task` 对应一次 `Pipeline.process()`。
- `run_task()` 状态流转：
  - PENDING → PROCESSING →（调用 `pipeline.process_tree(...)`）→ COMPLETED/FAILED
- **WS 进度推送**：采用 `subscribe → publish → unsubscribe` 模式。
  - 每个订阅队列 `Queue(maxsize=1)`，用于背压；慢消费者会丢弃中间进度，只保留最新一条。

## 4. 依赖的接口

Pipeline 是“全知”层，直接依赖所有处理模块：

| 来源 | 使用 |
|---|---|
| `models.py` | `PipelineResult/TaskProgress/MergedDocument/Gap/...` 等数据对象 |
| `pipeline/config.py` | `PipelineConfig` |
| `pipeline/scheduler.py` | `PipelineScheduler`（提供共享 `gpu_lock`） |
| `ocr/engine_manager.py` | `EngineManager`（按需切换引擎，管理 ppocr-server） |
| `ocr/base.py` | `OCREngine` Protocol |
| `processing/cleaner.py` | `OCRCleaner` |
| `processing/dedup.py` | `IncrementalMerger`（流式逐页增量合并） |
| `processing/segmenter.py` | `StreamSegmentExtractor`（流式增量切段） |
| `pipeline/rate_controller.py` | `RateController`（运行时自适应段长 L*，OCR/LLM 速率配速） |
| `llm/base.py` | `LLMRefiner` Protocol |
| `llm/cloud.py` | `CloudLLMRefiner`（云端实现：refine/fill_gap/final_refine + PII 实体检测） |
| `llm/local.py` | `LocalLLMRefiner`（本地实现：refine/fill_gap/final_refine） |
| `privacy/patterns.py` | 结构化 PII 正则（手机/邮箱/证件/银行卡等） |
| `privacy/redactor.py` | `PIIRedactor`（regex 脱敏 +（可选）云端实体检测 + 替换记录） |
| `output/renderer.py` | `Renderer`（渲染并写入最终 `document.md`） |

## 5. 编排流程图（流式：OCR 生产者 ∥ 流式消费者）

`process_many()`（实现 `_stream_pipeline`）启动两个并发协程，OCR 边产出、LLM 边消费，
`RateController` 在运行时自适应段长 L*。OCR 串行受 `gpu_lock` 保护，LLM 在引擎做精修时
GPU 已空出，OCR 可继续推进下一页——这是流式相对批量的核心收益。

```
Pipeline.process_many(image_dir, output_dir, ...) → PipelineResult（单文档）
    │
    ├─ scan_images(image_dir) → list[Path]
    ├─ controller = controller or RateController(llm)   # process_tree 传入共享实例
    ├─ page_queue: asyncio.Queue[PageOCR | None]
    ├─ 订阅熔断器 OPEN 事件 → llm_unavailable 进度帧（finally 里 unsubscribe）
    │
    ├──┬─ ① OCR 生产者 _ocr_producer（asyncio.Task）
    │  │    for each image:
    │  │      async with gpu_lock: page = engine.ocr(image, output_dir)
    │  │      cleaner.clean(page) → 质量检测 → 可选 redact_regex_only 逐页脱敏
    │  │      controller.record_ocr(dt, chars); page_queue.put(page)
    │  │      debug: {stem}_cleaned.md
    │  │    finally: page_queue.put(None)   # 哨兵，异常路径也必发
    │  │
    │  └─ ② 流式消费者 _stream_process
    │       merger = IncrementalMerger; extractor = StreamSegmentExtractor
    │       while (page := await page_queue.get()) is not None:
    │         merger.add_page(page)                       # 逐页增量合并去重
    │         if pii.enable and merger.page_count >= 5 and 未做过:
    │           entity_lexicon = _delayed_pii_detect(...)  # 收够页再拿 LLM lexicon
    │         _try_extract_and_refine(...)                 # 按 controller.target(L*) 切段
    │           → extractor.try_extract → refiner.refine（LLMCache 命中跳过；失败回退原文）
    │           → 累积 refined_results / all_gaps；controller.record_llm 反馈配速
    │       哨兵后：extract_remaining 处理尾段
    │       debug: merged_raw.md, rate_controller.json
    │
    └─ ③ 终结化 _finalize_single_doc（段全部收齐后）
         reassemble(refined_results) → doc            debug: reassembled.md
         → gap fill（可选 enable_gap_fill）：re-OCR(gpu_lock)+fill_gap，单 gap 异常降级
         → final refine（可选 enable_final_refine）：失败回退；重复 H2 触发一次带提示重做
         → 程序化兜底（0 LLM 成本）：dedup_html_tables / dedup_h2_sections
                                    / strip_code_block_line_numbers / strip_residual_ui_noise
         → parse_gaps 收残留 GAP → renderer.render → document.md
         → 汇总 warnings + extract_first_heading(doc_title) → PipelineResult
```

说明：

- **生产者/消费者解耦**：`page_queue` 作背压通道，`controller.set_queue_depth(qsize)` 反馈给配速器。
- **PII 流式策略**：OCR 生产阶段逐页 `redact_regex_only` 先行；满 `_PII_DETECT_THRESHOLD`（5）页后异步取一次 `EntityLexicon`，供 gap fill re-OCR 片段复用（详见 [privacy.md](privacy.md)）。
- **debug 中间产物**：定位各阶段差异，文件名以实现为准（`{stem}_cleaned.md` / `merged_raw.md` / `reassembled.md` / `rate_controller.json` 等）。
- **截断检测（truncation detection）**：识别 LLM 输出被长度截断的风险，以 warnings 透出，不中断流程。阈值见 [llm.md §6](llm.md#6-截断检测truncation-detection)。
- **代码模式**：`CodeRestoreConfig.enable=True` 时消费者换成 `_code_pipeline`，不做流式精修（详见 §10）。

## 6. 编程接口示例

```python
from pathlib import Path

from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline

pipeline = Pipeline(PipelineConfig())
await pipeline.initialize()

result = await pipeline.process_many(
    image_dir=Path("/path/to/photos"),
    output_dir=Path("/path/to/output"),
)
# result: PipelineResult（一个目录一篇文档）
# result.output_path          — .md 文件路径
# result.markdown             — markdown 内容
# result.warnings             — 流程警告信息（含截断检测等）
# result.redaction_records    — PII 脱敏统计（若启用）

await pipeline.shutdown()
```

> 多子目录输入请改用 `process_tree()`，它按叶子目录分别调用 `process_many()` 并把各自的单个 `PipelineResult` 聚合为 `list[PipelineResult]`（每个叶子一份）。

## 7. `_reassemble()` 拼接算法

```
_reassemble(refined_results: list[RefinedResult], merged_doc: MergedDocument) → MergedDocument:
    1. 取各段 refined_result.markdown
    2. 用 "\n" 拼接所有段
    3. 用拼接结果替换 merged_doc.markdown，保留 images 和 gaps
```

LLM 负责在精修时处理段间重叠的去重，`_reassemble()` 只做简单拼接。流式版在所有段从队列消费完毕后，于 `_finalize_single_doc()` 内调用本算法（再接 gap fill → final refine → 程序化兜底 → render）。

## 8. 错误处理策略

### 8.1 OCR 失败：Fail-fast

任一张照片 OCR 失败（GPU OOM、图片损坏等），整个任务立即标记为 FAILED，不跳过、不继续，避免产出不完整文档。

### 8.2 精修失败：重试后回退

- litellm 内置重试机制先处理瞬时错误（由 `LLMConfig.max_retries` 控制）
- 重试仍失败则该段/该阶段回退到未精修的原始 markdown，继续后续流程
- 最终产物可能有部分未精修段落，但尽量不丢内容

### 8.3 PII 脱敏失败策略（云端实体检测）

当启用 PII 脱敏且需要云端实体检测时：

- 若实体检测失败：
  - `PIIConfig.block_cloud_on_detect_failure=True` 时：标记 `cloud_blocked=True`，**跳过所有云端 LLM 阶段**（分段精修/缺口补充/整篇精修），仅产出 regex 脱敏后的结果，并记录 warning。
  - 若为 False：继续执行云端 LLM，但仍记录 warning。

### 8.4 Gap fill 失败策略：单 gap 异常降级

缺口补充阶段以“尽力而为”为原则：

- 单个 gap 的 re-OCR 或 fill_gap 失败：记录 warning，跳过该 gap，继续处理其他 gap 和后续流程。
- re-OCR 有缓存，避免同一页重复占用 GPU。

### 8.5 中间产物保留

任务失败时已生成的 `{stem}_OCR/` 目录及各阶段 debug 产物保留在 output_dir 中，便于排查与手动恢复。

### 8.6 API 错误格式（MVP）

开发阶段返回完整 traceback 方便调试，`Task.error` 字段保存完整错误信息；上线前再收紧为结构化错误。

## 9. 并发与资源策略

### 9.1 GPU 串行（asyncio.Lock）

- OCR 与 re-OCR 均通过 `asyncio.Lock` 串行化，避免多任务同时占用 GPU 导致 OOM。
- 推荐由 `PipelineScheduler.gpu_lock` 统一提供共享锁，实现跨任务串行。

### 9.2 LLM API 全局限流（asyncio.Semaphore）

- `PipelineScheduler.llm_semaphore` 由 `LLMConfig.max_concurrent_requests`（默认 3）
  构造，跨所有 pipeline 实例共享。
- `BaseLLMRefiner._call_llm()` 是所有 LLM 调用的统一出口：`refine` / `fill_gap` /
  `final_refine` / `detect_pii_entities` 全部经此限流。
- 注入路径：`api/app.py` lifespan 创建 Scheduler 后，
  `pipeline.set_llm_semaphore(scheduler.llm_semaphore)` → `Pipeline._create_refiner()`
  构造 `CloudLLMRefiner(cfg, semaphore=self._llm_semaphore)`。
- **Gap fill 三段锁序**（非嵌套，无死锁）：
  1. 分段 refine：持 `llm_semaphore` 调用 LLM；
  2. Re-OCR：释放 `llm_semaphore`，改持 `gpu_lock` 调用 `reocr_page`；
  3. `fill_gap`：释放 `gpu_lock`，重新获取 `llm_semaphore` 调用 LLM。

> 历史：`QueueConfig.max_concurrent_pipelines` / `pipeline_semaphore` 已废弃。
> 原因：粗粒度 pipeline 计数无法保护 API 限流；改为细粒度 LLM 调用计数，
> 语义更精确，OCR 仍由 `gpu_lock` 强制串行。

### 9.3 子目录并行（process_tree）

`process_tree` 发现 image_dir 下有多个叶子子目录时，用 `asyncio.gather` 并行调用
`process_many`，而不是串行 for 循环。每个子目录内部仍完整走 OCR → PII → LLM →
render 流水。实际并发度由底层锁决定：

- **OCR**：`gpu_lock` 强制串行（峰值并发 ≤ 1），防止 GPU OOM；
- **LLM**：`llm_semaphore` 限流（默认 3），多子目录的 refine/gap_fill 可并发进入；
- **PII / dedup / reassemble / render**：纯 CPU / IO，完全并行。

因此 subdir 1 进入 LLM 阶段（已释放 `gpu_lock`）时，subdir 2 的 OCR 可立即启动，
避免"做 LLM 时 GPU 空闲"。任一子目录抛异常即 `asyncio.gather` 整体 fail-fast，
上层 `TaskManager` 将任务标记 FAILED（与串行语义一致）。

> 测试：`tests/pipeline/test_process_tree.py` 覆盖单/多子目录入口与并行分支。

### 9.4 无组级并发

聚类已移除，所有图片视为同一份文档，因此不存在”组级并发”或”按组分裂任务”的调度逻辑。所有并发策略以”任务级”为边界。

> **历史**：曾有「LLM 文档聚类」设计——精修阶段检测 `DOC_BOUNDARY` 标记把合并文本拆成多个子文档。该路径连同 `parse_doc_boundaries` / `detect_doc_boundaries` / `DocBoundary` 等符号已于 2026-05-29 彻底删除（代码模式用独立的 `group_into_files` 聚合，从未复用 DOC_BOUNDARY；文档模式也不再用）。现在「一个叶子目录 = 一篇文档」，`process_many()` 只返回单个 `PipelineResult`。设计反转由来见 [references/streaming-pipeline.md](references/streaming-pipeline.md)。

## 10. 代码模式编排（`CodeRestoreConfig.enable=True`）

`PipelineConfig.code.enable=True` 时，`_stream_pipeline` 在启动 OCR 生产者后按 `code_cfg.enable` 二选一：消费者换成代码模式专用分支 `_code_pipeline`（而非 `_stream_process`），跳过普通模式的流式精修 / 增量合并 / 切段链路。

### 10.1 OCR 引擎强制 basic

代码模式强制把 OCR 切到 PaddleOCR `basic` pipeline（PP-OCRv5），因为只有 basic 输出行级 `text_lines`（含 bbox + 文本），代码栏组装依赖该输入；VL pipeline 不产 text_lines，启用代码模式会因无可组装内容而失败。请求级 `ocr` 覆盖经 `_ocr_config_for_code_mode` 统一改写，避免每个调用点重复判断（B4 H5）。

### 10.2 编排流程图（OCR 生产者 → 代码消费者顺序执行）

与文档模式不同，代码模式的消费者**不流式**：先把 OCR 队列排空，再对收齐的页顺序跑代码链。
OCR 生产者仍与文档模式共用（`gpu_lock` 串行），只是消费端换成 `_code_pipeline`。

```
Pipeline.process_many(code.enable=True) → PipelineResult（markdown=""）
    │
    ├─ ① OCR 生产者 _ocr_producer（同文档模式，gpu_lock 串行）→ page_queue
    │     代码模式经 _ocr_config_for_code_mode 强制 PaddleOCR basic（产 text_lines）
    │
    └─ ② 代码消费者 _code_pipeline（排空 page_queue 后顺序执行）
        │
        ├─ 1. 排空队列至哨兵；pages_ref 由 producer 填充（为空 → RuntimeError）
        │
        ├─ 2. 逐图组装 PageColumn（progress: code_layout）
        │     for page in pages_ref：
        │       无 text_lines → 记 missing_line_pages 并跳过
        │       analyze_layout(text_lines, image_size)
        │       [rerun_column_ocr*]            # code_cfg.secondary_column_ocr=True 才跑
        │       extract_ide_metas → [_augment_metas_with_code_context]  # context_root 提供时
        │       assemble_columns → PageColumn[]
        │     all_pcs 为空 → RuntimeError（区分“无行级输出” vs “无可组装代码列”）
        │
        ├─ 3. group_into_files(all_pcs) → SourceFile[]（progress: code_group）
        │     3.1 clean_code_ocr_text   OCR 保守纠错（行数保持，PII/LLM 之前）
        │     3.2 diagnose_source_files 预诊断 → pre_refine_diagnostics_by_path
        │     3.5 PII _redact_code_headers   仅 leading comment block（regex+lexicon+自定义词）
        │
        ├─ 4. LLM 精修（每个 SourceFile 独立串行；catch Exception 回退原文）（progress: code_refine）
        │     for src in sources：
        │       ├─ syntax_dirty   → DiagnosticCodeRepairer.repair → 重诊断 → CodeConsistencyAuditor.audit
        │       ├─ 大文件超阈值   → 跳过，标 code.repair.skipped_large_file_no_window
        │       └─ 其他           → CodeLLMRefiner.refine（mode=refine|rewrite）
        │
        ├─ 5. render_code_files → output_dir/files/<相对路径> + files-index.json + document.md
        │     detect_code_mode_quality → .quality_report.json（progress: code_render）
        │
        └─ PipelineResult(output_path=document.md, markdown="",
                          warnings=["code_mode: N files, M skipped"])
```

`*` 表示 `code_cfg.secondary_column_ocr=True` 时对识别出的 column 裁剪增强后二次 OCR（默认关）。

### 10.3 错误处理

- **整图无 text_lines**：跳过该页并记入 `missing_line_pages`，列表用于上报；其他页继续。
- **所有页都无 column**：`raise RuntimeError("代码模式：OCR producer 未产出任何页")`，由上层任务捕获写错误结果。
- **单 SourceFile 的 LLM 精修/repair/audit 失败**：`catch Exception` 回退原文，写日志和 quality flag，不中断同任务其他文件。
- **PII 失败**（云端实体检测异常）：与普通模式一致，单 SourceFile 降级跳过。
- **诊断器外部工具缺失**：`CodeDiagnosticRunner` 降级为 `tool_unavailable`，不让任务失败（见 [processing.md §3.5](processing.md)）。

### 10.4 并发与资源

- OCR producer 与 `_code_pipeline` 通过 `page_queue` 解耦，OCR 受 `gpu_lock` 串行；`_code_pipeline` 在 OCR 队列排空后顺序执行后续阶段。
- LLM 精修/repair/audit **逐文件串行**，不并发（避免对 LLM provider 同时打多个长上下文请求触发限流；当前 SourceFile 数量通常 ≤ 几十，串行可控）。
- 阻塞 IO（`diagnose_source_files`、`build_repair_contexts` 的 rglob/read_text）统一用 `asyncio.to_thread` 移出事件循环（B7 C12 / S3）。

### 10.5 输出与兼容

`_code_pipeline` 返回 `PipelineResult(output_path=document.md, markdown="")`：
- `output_path` 指向 `output_dir/document.md`（占位，兼容旧 UI 路由）
- `markdown` 为空，前端通过 `files-index.json` 单独渲染代码模式审查视图（见 [frontend/features.md §7](../frontend/features.md)）
- `warnings` 写一条 `code_mode: N files, M skipped` 摘要

## 11. 相关文档

- [数据模型](data-models.md)
- [OCR 层](ocr.md)
- [LLM 层](llm.md)
- [API 层](api.md)