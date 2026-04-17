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

> 历史变更：早期基于坐标 / 文本特征的聚类已移除。现在多文档识别由 **LLM 文档边界检测**（`LLMRefiner.detect_doc_boundaries()`）接管，见 §10。单文档场景下返回 `list[PipelineResult]` 长度为 1。

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
    ) -> list[PipelineResult]: ...

    async def shutdown(self) -> None: ...
```

**调用约定**：

- 必须先 `initialize()` 再 `process_tree()` / `process_many()`；任务结束后调用 `shutdown()` 释放资源。
- `process_tree()` 是统一入口：自动识别单/多子目录结构，最终委托给 `process_many()`。
- `process_many()` 返回 `list[PipelineResult]`（支持多文档输出）。
- **多文档处理**：reassemble 之后调用 `refiner.detect_doc_boundaries()`（独立 LLM 调用，返回 `list[DocBoundary]`），按边界拆分为多个子文档。每个子文档独立 gap fill → final refine → render，产物落在 `{output_dir}/{sanitized_title}/document.md`。
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
| `processing/dedup.py` | `PageDeduplicator` |
| `processing/segmenter.py` | `DocumentSegmenter` |
| `llm/base.py` | `LLMRefiner` Protocol |
| `llm/cloud.py` | `CloudLLMRefiner`（云端实现：refine/fill_gap/final_refine + PII 实体检测） |
| `llm/local.py` | `LocalLLMRefiner`（本地实现：refine/fill_gap/final_refine） |
| `privacy/patterns.py` | 结构化 PII 正则（手机/邮箱/证件/银行卡等） |
| `privacy/redactor.py` | `PIIRedactor`（regex 脱敏 +（可选）云端实体检测 + 替换记录） |
| `output/renderer.py` | `Renderer`（渲染并写入最终 `document.md`） |

## 5. 编排流程图

```
Pipeline.process_many(image_dir, output_dir, on_progress?, gpu_lock?) → list[PipelineResult]
    │
    ├─ scan_images(image_dir) → list[Path]
    │
    ├─ OCR + Clean（GPU Lock 保护）
    │  engine = await engine_manager.ensure(ocr)  # 按需切换引擎（ocr 为完整 OCRConfig 快照 or None）
    │  for each image:
    │    async with gpu_lock:
    │      page = await engine.ocr(image, output_dir)
    │    await cleaner.clean(page)
    │
    ├─ 去重合并
    │  dedup.merge_all_pages(pages) → MergedDocument
    │  debug: merged_raw.md
    │
    ├─ PII 脱敏（可选，PIIConfig.enable=True）
    │  PIIRedactor.redact_for_cloud()
    │    → (MergedDocument, RedactionRecord[], EntityLexicon?, cloud_blocked)
    │  debug: after_pii.md
    │
    ├─ 分段精修（若未被 cloud_blocked）
    │  segmenter.segment() → list[Segment]
    │  for seg in segments:
    │    refiner.refine(seg.text, context) → RefinedResult（失败回退原文）
    │  截断检测：finish_reason=="length" 或行数比例启发式（阈值见
    │    LLMConfig.truncation_ratio_threshold / truncation_min_input_lines）→ warnings
    │
    ├─ 重组
    │  _reassemble(refined_results, merged_doc) → MergedDocument
    │  debug: reassembled.md
    │
    ├─ 缺口自动补充（可选，LLMConfig.enable_gap_fill=True）
    │  for gap in merged_doc.gaps:
    │    re-OCR（GPU Lock 保护）：reocr_page() → re-OCR 文本
    │    fill_gap() → 生成补全文本并插入
    │    re-OCR 缓存 + 单 gap 异常降级
    │  debug: after_gap_fill.md
    │
    ├─ 整篇精修（可选，LLMConfig.enable_final_refine=True）
    │  final_refine(markdown) → RefinedResult（失败回退原文）
    │  debug: final_refined.md
    │
    ├─ 解析残留 GAP 标记 → Gap 列表
    │
    ├─ 输出
    │  renderer.render(document, output_dir) → document.md
    │
    └─ 汇总 warnings → PipelineResult
```

说明：

- **debug 中间产物**：用于定位 OCR/脱敏/精修/补缺阶段的差异，文件名以实现为准（例如 `merged_raw.md`、`after_pii.md` 等）。
- **截断检测（truncation detection）**：用于识别 LLM 输出被上下文/长度截断的风险，并以 warnings 形式透出；不直接中断流程。阈值细节见 [llm.md §6](llm.md#6-截断检测truncation-detection)。

## 6. 编程接口示例

```python
from pathlib import Path

from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline

pipeline = Pipeline(PipelineConfig())
await pipeline.initialize()

results = await pipeline.process_many(
    image_dir=Path("/path/to/photos"),
    output_dir=Path("/path/to/output"),
)
# results: list[PipelineResult]（LLM 聚类可能拆成多份）
# results[0].output_path          — .md 文件路径
# results[0].markdown             — markdown 内容
# results[0].warnings             — 流程警告信息（含截断检测等）
# results[0].redaction_records    — PII 脱敏统计（若启用）

await pipeline.shutdown()
```

> 多子目录输入请改用 `process_tree()`，它会按叶子目录分别调用 `process_many()` 并聚合结果。

## 7. `_reassemble()` 拼接算法

```
_reassemble(refined_results: list[RefinedResult], merged_doc: MergedDocument) → MergedDocument:
    1. 取各段 refined_result.markdown
    2. 用 "\n" 拼接所有段
    3. 用拼接结果替换 merged_doc.markdown，保留 images 和 gaps
```

LLM 负责在精修时处理段间重叠的去重，`_reassemble()` 只做简单拼接。

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
  `final_refine` / `detect_doc_boundaries` / `detect_pii_entities` 全部经此限流。
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

### 9.3 无组级并发

聚类已移除，所有图片视为同一份文档，因此不存在”组级并发”或”按组分裂任务”的调度逻辑。所有并发策略以”任务级”为边界。

## 10. 多文档处理（LLM 文档聚类）

### 10.1 概述

通过 LLM 精修阶段检测文档边界标记 `DOC_BOUNDARY`，自动拆分为多个子文档，每个独立输出。

### 10.2 工作流程

```
OCR → 清洗 → 去重合并 → PII 脱敏 → 分段精修（检测 DOC_BOUNDARY）
    → 拆分子文档 → 每个子文档独立：gap fill → final refine → render
```

### 10.3 文档边界检测

- 分段精修 + reassemble 完成后，Pipeline 调用 `refiner.detect_doc_boundaries(merged_markdown)`
- LLM 返回 JSON 数组：`[{"after_page": "page12.jpg", "new_title": "第二篇标题"}, ...]`
- `llm/prompts.py::parse_doc_boundaries()` 做 JSON 容错：解析失败或非数组时降级为 `[]`（单文档）
- 无标题子文档由 `extract_first_heading()` 兜底命名；目录名通过 `utils/paths.sanitize_dirname()` + `dedupe_dirnames()` 去非法字符并去重

### 10.4 输出结构

- 单文档：`{output_dir}/document.md`
- 多文档：`{output_dir}/{sanitized_title}/document.md`（`PipelineResult.doc_dir` 记录相对子目录名，`doc_title` 记录原始标题）

### 10.5 API 兼容性

- `Pipeline.process_many()` / `process_tree()` 一律返回 `list[PipelineResult]`
- `Task.results: list[PipelineResult]`；API `GET /tasks/{id}/results` 返回多文档列表，`GET /tasks/{id}/result` 返回列表首项（兼容）

## 11. 相关文档

- [数据模型](data-models.md)
- [OCR 层](ocr.md)
- [LLM 层](llm.md)
- [API 层](api.md)