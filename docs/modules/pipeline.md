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

串联 OCR → 清洗 → 去重 → LLM 精修 → 输出的完整流程，管理任务生命周期和进度上报。是唯一知道所有处理层模块存在的层。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `pipeline/config.py` | `PipelineConfig` 总配置（详见 [models.md](models.md)） |
| `pipeline/pipeline.py` | `Pipeline` 核心编排器 |
| `pipeline/task_manager.py` | `TaskManager` 任务生命周期管理 |

## 3. 对外接口

### 3.1 Pipeline（pipeline/pipeline.py）

API 层和编程接口通过此类驱动整个处理流程。

```python
class Pipeline:
    """核心编排器"""

    def __init__(self, config: PipelineConfig) -> None: ...

    async def initialize(self) -> None:
        """创建并初始化 OCR 引擎（加载模型）+ LLM 精修器"""

    async def process(
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None] | None = None,
    ) -> PipelineResult:
        """
        完整处理流程：
        阶段 1 OCR：收集 image_dir 下 *.jpg/*.png/*.jpeg 并按文件名排序 → ocr_batch()
        阶段 2 清洗：逐页 await cleaner.clean() → 填充 cleaned_text
        阶段 3 去重：deduplicator.merge_all_pages() → MergedDocument
        阶段 4 精修：segmenter.segment() → 逐段 refiner.refine() → _reassemble()
        阶段 5 缺口：收集各段 gaps → PipelineResult.gaps（MVP 仅标记，不自动补充）
        阶段 6 输出：renderer.render() → document.md → 构造 PipelineResult
        """

    async def shutdown(self) -> None:
        """释放所有资源（OCR 引擎 GPU 等）"""
```

**调用约定**：
- 必须先 `initialize()` 再 `process()`
- `on_progress` 回调在每个阶段的每一步触发，传递 `TaskProgress`
- `process()` 返回 `PipelineResult`（含 output_path + markdown），同时最终文件已写入 `output_dir`
- 使用完毕调用 `shutdown()` 释放资源

### 3.2 TaskManager（pipeline/task_manager.py）

API 层通过此类管理异步任务。

```python
class TaskStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class Task:
    task_id: str                       # uuid hex[:8]
    status: TaskStatus
    image_dir: str
    output_dir: str
    progress: TaskProgress | None
    result: PipelineResult | None
    error: str | None
    created_at: datetime

class TaskManager:
    """任务生命周期管理（内存存储，MVP 不持久化）"""

    def __init__(self, pipeline: Pipeline) -> None: ...

    def create_task(self, image_dir: str, output_dir: str | None = None) -> Task:
        """创建任务，状态为 PENDING。output_dir 为空时自动生成"""

    async def run_task(self, task_id: str) -> None:
        """PENDING → PROCESSING → pipeline.process() → COMPLETED / FAILED"""

    def get_task(self, task_id: str) -> Task | None:
        """查询任务状态"""
```

**调用约定**：
- `create_task()` 只创建记录，不启动处理
- `run_task()` 由 API 层通过 `BackgroundTasks` 调度
- 任务状态通过 `get_task()` 轮询（MVP 不含 WebSocket）

## 4. 依赖的接口

Pipeline 是唯一的"全知"模块，依赖所有处理层：

| 来源 | 使用 |
|---|---|
| `models.py` | 全部数据对象 |
| `pipeline/config.py` | `PipelineConfig` |
| `ocr/base.py` | `OCREngine` Protocol |
| `ocr/deepseek_ocr2.py` | `DeepSeekOCR2Engine`（具体实现） |
| `processing/cleaner.py` | `OCRCleaner` |
| `processing/dedup.py` | `PageDeduplicator` |
| `llm/base.py` | `LLMRefiner` Protocol |
| `llm/cloud.py` | `CloudLLMRefiner`（具体实现） |
| `llm/segmenter.py` | `DocumentSegmenter` |
| `output/renderer.py` | `Renderer` |

## 5. 编排流程图

```
Pipeline.process(image_dir, output_dir)
    │
    ├─ 阶段 1: OCR
    │  ocr_engine.ocr_batch(sorted_images, output_dir) → list[PageOCR]
    │  进度：stage="ocr", current=1..N, total=N
    │
    ├─ 阶段 2: 清洗
    │  for page in pages: await cleaner.clean(page)
    │  进度：stage="clean", current=1..N, total=N
    │
    ├─ 阶段 3: 去重合并
    │  deduplicator.merge_all_pages(pages) → MergedDocument
    │  进度：stage="merge", current=1..N-1, total=N-1
    │
    ├─ 阶段 4: LLM 精修
    │  segments = segmenter.segment(merged.markdown)
    │  for seg in segments:
    │    try: refiner.refine(seg.text, context) → RefinedResult
    │    except: 回退到 seg.text（未精修原文），继续后续段落
    │  document = _reassemble(refined_results, merged_doc) → MergedDocument
    │  进度：stage="refine", current=1..M, total=M
    │
    ├─ 阶段 5: 缺口标记（MVP 仅标记，不自动补充）
    │  收集各段 RefinedResult.gaps → PipelineResult.gaps
    │
    └─ 阶段 6: 输出
       renderer.render(document, output_dir) → document.md
       进度：stage="render"
```

## 6. 编程接口示例

```python
from docrestore import Pipeline, PipelineConfig

config = PipelineConfig(
    llm=LLMConfig(model="anthropic/claude-sonnet-4-20250514"),
)

pipeline = Pipeline(config)
await pipeline.initialize()

result = await pipeline.process(
    image_dir=Path("/path/to/photos"),
    output_dir=Path("/path/to/output"),
)
# result: PipelineResult
# result.output_path  — 最终 .md 文件路径
# result.markdown     — 最终 markdown 内容

await pipeline.shutdown()
```

## 7. `_reassemble()` 拼接算法

Segmenter 切段时给每段的重叠区域包裹 `<!-- overlap-start -->` / `<!-- overlap-end -->` 标记，LLM prompt 规则 3 已要求保留这些标记。

```
_reassemble(refined_results: list[RefinedResult], merged_doc: MergedDocument) → MergedDocument:
    1. 对每段 refined markdown：
       - 移除 <!-- overlap-start --> 到 <!-- overlap-end --> 之间的内容（含标记本身）
       - 第一段保留开头的 overlap 区域（无前段可裁剪）
       - 最后一段保留结尾的 overlap 区域（无后段可裁剪）
    2. 按段序拼接裁剪后的文本
    3. 用拼接结果替换 merged_doc.markdown，保留 images 和 gaps
```

此方案不依赖行号，对 LLM 增删行具有鲁棒性。

## 8. 错误处理策略

### 8.1 OCR 失败：Fail-fast

任一张照片 OCR 失败（GPU OOM、图片损坏等），整个任务立即标记为 FAILED，不跳过、不继续，避免产出不完整文档。

### 8.2 精修失败：重试后回退

- litellm 内置重试机制先处理瞬时错误（由 `LLMConfig.max_retries` 控制）
- 重试仍失败则该段回退到未精修的原始 markdown，继续后续流程
- 最终产物可能有部分未精修段落，但不丢内容

### 8.3 中间产物保留

任务失败时已生成的 `{stem}_OCR/` 目录保留在 output_dir 中，方便排查问题和手动恢复。

### 8.4 API 错误格式（MVP）

开发阶段返回完整 traceback 方便调试，`Task.error` 字段存完整错误信息。上线前再收紧为结构化错误。

## 9. 并发与资源策略

### 9.1 MVP 串行任务队列

- TaskManager 一次只允许一个任务处于 PROCESSING 状态
- 新任务排队等待（PENDING），前一个完成后自动开始下一个
- 预留未来扩展：接口不限制并发，串行约束仅在 TaskManager 实现层

### 9.2 asyncio.Lock 防竞态

TaskManager 的任务表操作（创建/更新状态/查询）加 `asyncio.Lock`，防止并发请求导致状态不一致。