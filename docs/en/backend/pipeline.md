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

# Pipeline Orchestration Layer (pipeline/)

## 1. Responsibilities

The Pipeline is the end-to-end orchestration layer responsible for chaining all processing modules in a defined order, while providing unified management of task lifecycle, progress reporting, and GPU resources.

Core responsibilities:

- **Document processing pipeline**:
  OCR -> Clean -> Dedup & Merge -> PII Redaction (optional) -> Segment Refinement -> Reassemble -> Gap Fill (optional) -> Final Refinement (optional) -> Output
- **Task lifecycle**: Driven by `TaskManager` calling `Pipeline.process()`, maintaining task states (PENDING/PROCESSING/COMPLETED/FAILED).
- **Progress reporting**: Continuously pushes `TaskProgress` via the `on_progress` callback (forwarded by the API/WS layer).
- **Concurrency & resources**:
  - GPU serialization: OCR and re-OCR use `asyncio.Lock` for serialization (cross-task shared lock provided by Scheduler).
  - LLM rate limiting: All LLM API calls (refine / fill_gap / final_refine / detect_*) are gated by `scheduler.llm_semaphore`
    (constructed from `LLMConfig.max_concurrent_requests`). The cap applies across **all concurrently running pipelines**. See Section 9.2.

> Historical note: Earlier coordinate-based / text-feature clustering has been removed. Multi-document recognition is now handled by **LLM document boundary detection** (`LLMRefiner.detect_doc_boundaries()`); see Section 10. In single-document scenarios, `list[PipelineResult]` has length 1.

## 2. File List

| File | Responsibility |
|---|---|
| `pipeline/config.py` | `PipelineConfig` master configuration (includes `db_path`; see [data-models.md](data-models.md)) |
| `pipeline/pipeline.py` | `Pipeline` core orchestrator |
| `pipeline/task_manager.py` | `TaskManager` task lifecycle management |
| `pipeline/scheduler.py` | `PipelineScheduler` global scheduler (see [scheduler.md](scheduler.md)) |

## 3. Public Interface

### 3.1 Pipeline (pipeline/pipeline.py)

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

**Calling conventions**:

- You must call `initialize()` before `process_tree()` / `process_many()`; call `shutdown()` after tasks complete to release resources.
- `process_tree()` is the unified entry point: it automatically detects single/multi-subdirectory structures and ultimately delegates to `process_many()`.
- `process_many()` returns `list[PipelineResult]` (supporting multi-document output).
- **Multi-document processing**: After reassembly, calls `refiner.detect_doc_boundaries()` (a separate LLM call returning `list[DocBoundary]`), then splits into multiple sub-documents by boundary. Each sub-document independently undergoes gap fill -> final refine -> render, with artifacts placed in `{output_dir}/{sanitized_title}/document.md`.
- `gpu_lock`:
  - When provided by `PipelineScheduler`, enables **cross-task** OCR/re-OCR serialization;
  - When omitted, Pipeline creates a default lock that only guarantees serialization **within a single call**.
- `llm` / `ocr` / `pii`: **Complete Config snapshots** representing the final configuration for this request; when `None`, Pipeline uses the defaults from `self.config`. Pipeline no longer performs "default dict + override dict" merging internally -- this synthesis is done once by the API route layer upon receiving the request.
- **EngineManager integration**: After calling `set_engine_manager()`, OCR engine initialization is deferred -- the first OCR call triggers `EngineManager.ensure()` to create the engine on demand. `set_ocr_engine()` remains available for test injection.

### 3.2 TaskManager (pipeline/task_manager.py)

```python
@dataclass
class Task:
    task_id: str
    status: TaskStatus  # PENDING / PROCESSING / COMPLETED / FAILED
    image_dir: str
    output_dir: str
    llm: LLMConfig | None = None       # Complete snapshot; None means use defaults
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
    def pipeline(self) -> Pipeline: ...  # Allows API layer to read default Config for request synthesis

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

**Key behaviors**:

- **No parent-child tasks**: Each `Task` corresponds to a single `Pipeline.process()` call.
- `run_task()` state transitions:
  - PENDING -> PROCESSING -> (calls `pipeline.process_tree(...)`) -> COMPLETED/FAILED
- **WS progress push**: Uses a `subscribe -> publish -> unsubscribe` pattern.
  - Each subscription queue is `Queue(maxsize=1)` for back-pressure; slow consumers discard intermediate progress updates, keeping only the latest.

## 4. Dependencies

Pipeline is the "omniscient" layer, directly depending on all processing modules:

| Source | Usage |
|---|---|
| `models.py` | `PipelineResult/TaskProgress/MergedDocument/Gap/...` data objects |
| `pipeline/config.py` | `PipelineConfig` |
| `pipeline/scheduler.py` | `PipelineScheduler` (provides shared `gpu_lock`) |
| `ocr/engine_manager.py` | `EngineManager` (on-demand engine switching, ppocr-server management) |
| `ocr/base.py` | `OCREngine` Protocol |
| `processing/cleaner.py` | `OCRCleaner` |
| `processing/dedup.py` | `PageDeduplicator` |
| `processing/segmenter.py` | `DocumentSegmenter` |
| `llm/base.py` | `LLMRefiner` Protocol |
| `llm/cloud.py` | `CloudLLMRefiner` (cloud implementation: refine/fill_gap/final_refine + PII entity detection) |
| `llm/local.py` | `LocalLLMRefiner` (local implementation: refine/fill_gap/final_refine) |
| `privacy/patterns.py` | Structured PII regexes (phone/email/ID/bank card, etc.) |
| `privacy/redactor.py` | `PIIRedactor` (regex redaction + (optional) cloud entity detection + redaction records) |
| `output/renderer.py` | `Renderer` (renders and writes the final `document.md`) |

## 5. Orchestration Flow Diagram

```
Pipeline.process_many(image_dir, output_dir, on_progress?, gpu_lock?) -> list[PipelineResult]
    |
    |-- scan_images(image_dir) -> list[Path]
    |
    |-- OCR + Clean (GPU Lock protected)
    |  engine = await engine_manager.ensure(ocr)  # On-demand engine switching (ocr is a full OCRConfig snapshot or None)
    |  for each image:
    |    async with gpu_lock:
    |      page = await engine.ocr(image, output_dir)
    |    await cleaner.clean(page)
    |
    |-- Dedup & Merge
    |  dedup.merge_all_pages(pages) -> MergedDocument
    |  debug: merged_raw.md
    |
    |-- PII Redaction (optional, PIIConfig.enable=True)
    |  PIIRedactor.redact_for_cloud()
    |    -> (MergedDocument, RedactionRecord[], EntityLexicon?, cloud_blocked)
    |  debug: after_pii.md
    |
    |-- Segment Refinement (if not cloud_blocked)
    |  segmenter.segment() -> list[Segment]
    |  for seg in segments:
    |    refiner.refine(seg.text, context) -> RefinedResult (falls back to original on failure)
    |  Truncation detection: finish_reason=="length" or line-count ratio heuristic (thresholds:
    |    LLMConfig.truncation_ratio_threshold / truncation_min_input_lines) -> warnings
    |
    |-- Reassemble
    |  _reassemble(refined_results, merged_doc) -> MergedDocument
    |  debug: reassembled.md
    |
    |-- Gap Auto-Fill (optional, LLMConfig.enable_gap_fill=True)
    |  for gap in merged_doc.gaps:
    |    re-OCR (GPU Lock protected): reocr_page() -> re-OCR text
    |    fill_gap() -> generate fill text and insert
    |    re-OCR cache + per-gap exception degradation
    |  debug: after_gap_fill.md
    |
    |-- Final Refinement (optional, LLMConfig.enable_final_refine=True)
    |  final_refine(markdown) -> RefinedResult (falls back to original on failure)
    |  debug: final_refined.md
    |
    |-- Parse residual GAP markers -> Gap list
    |
    |-- Output
    |  renderer.render(document, output_dir) -> document.md
    |
    +-- Aggregate warnings -> PipelineResult
```

Notes:

- **Debug intermediate artifacts**: Used for diagnosing differences across OCR/redaction/refinement/gap-fill stages; filenames are implementation-defined (e.g. `merged_raw.md`, `after_pii.md`, etc.).
- **Truncation detection**: Identifies the risk of LLM output being truncated by context/length limits and surfaces it as warnings; does not interrupt the pipeline. For threshold details, see [llm.md Section 6](llm.md#6-truncation-detection).

## 6. Programming Interface Example

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
# results: list[PipelineResult] (LLM clustering may split into multiple documents)
# results[0].output_path          -- .md file path
# results[0].markdown             -- markdown content
# results[0].warnings             -- pipeline warnings (including truncation detection, etc.)
# results[0].redaction_records    -- PII redaction statistics (if enabled)

await pipeline.shutdown()
```

> For multi-subdirectory input, use `process_tree()` instead -- it calls `process_many()` for each leaf directory and aggregates results.

## 7. `_reassemble()` Concatenation Algorithm

```
_reassemble(refined_results: list[RefinedResult], merged_doc: MergedDocument) -> MergedDocument:
    1. Take each segment's refined_result.markdown
    2. Join all segments with "\n"
    3. Replace merged_doc.markdown with the joined result, preserving images and gaps
```

The LLM is responsible for deduplicating inter-segment overlaps during refinement; `_reassemble()` only performs simple concatenation.

## 8. Error Handling Strategies

### 8.1 OCR Failure: Fail-fast

If any photo's OCR fails (GPU OOM, corrupted image, etc.), the entire task is immediately marked as FAILED -- no skipping, no continuing -- to avoid producing incomplete documents.

### 8.2 Refinement Failure: Retry then Fallback

- litellm's built-in retry mechanism handles transient errors first (controlled by `LLMConfig.max_retries`)
- If retries are exhausted, the segment/stage falls back to the unrefined original markdown and continues the remaining pipeline
- The final output may contain some unrefined segments, but content loss is minimized

### 8.3 PII Redaction Failure Strategy (Cloud Entity Detection)

When PII redaction is enabled and cloud entity detection is required:

- If entity detection fails:
  - `PIIConfig.block_cloud_on_detect_failure=True`: Sets `cloud_blocked=True`, **skips all cloud LLM stages** (segment refinement/gap fill/final refinement), outputs only regex-redacted results, and logs a warning.
  - If False: Continues with cloud LLM, but still logs a warning.

### 8.4 Gap Fill Failure Strategy: Per-gap Degradation

The gap fill stage operates on a best-effort basis:

- If a single gap's re-OCR or fill_gap fails: logs a warning, skips that gap, and continues processing other gaps and subsequent stages.
- Re-OCR results are cached to avoid redundant GPU usage for the same page.

### 8.5 Intermediate Artifact Retention

When a task fails, the already-generated `{stem}_OCR/` directories and debug artifacts from each stage are retained in output_dir for investigation and manual recovery.

### 8.6 API Error Format (MVP)

During development, full tracebacks are returned for debugging convenience. The `Task.error` field stores the complete error message; this will be tightened to structured errors before production release.

## 9. Concurrency & Resource Strategy

### 9.1 GPU Serialization (asyncio.Lock)

- Both OCR and re-OCR are serialized via `asyncio.Lock` to prevent multiple tasks from simultaneously occupying the GPU and causing OOM.
- It is recommended that `PipelineScheduler.gpu_lock` provide a unified shared lock for cross-task serialization.

### 9.2 Global LLM API Rate Limiting (asyncio.Semaphore)

- `PipelineScheduler.llm_semaphore` is constructed from `LLMConfig.max_concurrent_requests`
  (default 3) and shared across every pipeline instance.
- `BaseLLMRefiner._call_llm()` is the single entry point for every LLM call
  (`refine` / `fill_gap` / `final_refine` / `detect_doc_boundaries` / `detect_pii_entities`);
  all of them are rate-limited through this gate.
- Injection path: `api/app.py` lifespan creates the Scheduler, then
  `pipeline.set_llm_semaphore(scheduler.llm_semaphore)` → `Pipeline._create_refiner()`
  builds `CloudLLMRefiner(cfg, semaphore=self._llm_semaphore)`.
- **Gap fill three-stage lock sequence** (non-nested, deadlock-free):
  1. Segment refine: holds `llm_semaphore`, calls LLM;
  2. Re-OCR: releases `llm_semaphore`, acquires `gpu_lock`, calls `reocr_page`;
  3. `fill_gap`: releases `gpu_lock`, re-acquires `llm_semaphore`, calls LLM.

> Historical note: `QueueConfig.max_concurrent_pipelines` / `pipeline_semaphore` have been removed.
> Reason: the coarse-grained pipeline counter cannot enforce API quotas;
> the finer-grained per-LLM-call counter is semantically precise. OCR is still
> forced to be serial by `gpu_lock`.

### 9.3 No Group-level Concurrency

Clustering has been removed -- all images are treated as a single document, so there is no "group-level concurrency" or "split-by-group task" scheduling logic. All concurrency strategies are bounded at the task level.

## 10. Multi-document Processing (LLM Document Clustering)

### 10.1 Overview

Document boundary markers (`DOC_BOUNDARY`) detected during LLM refinement automatically split the content into multiple sub-documents, each output independently.

### 10.2 Workflow

```
OCR -> Clean -> Dedup & Merge -> PII Redaction -> Segment Refinement (detect DOC_BOUNDARY)
    -> Split into sub-documents -> Each sub-document independently: gap fill -> final refine -> render
```

### 10.3 Document Boundary Detection

- After segment refinement + reassembly, Pipeline calls `refiner.detect_doc_boundaries(merged_markdown)`
- The LLM returns a JSON array: `[{"after_page": "page12.jpg", "new_title": "Second Document Title"}, ...]`
- `llm/prompts.py::parse_doc_boundaries()` performs JSON fault tolerance: on parse failure or non-array result, degrades to `[]` (single document)
- Untitled sub-documents are named by `extract_first_heading()` as a fallback; directory names are sanitized via `utils/paths.sanitize_dirname()` + `dedupe_dirnames()` to remove illegal characters and deduplicate

### 10.4 Output Structure

- Single document: `{output_dir}/document.md`
- Multiple documents: `{output_dir}/{sanitized_title}/document.md` (`PipelineResult.doc_dir` records the relative subdirectory name, `doc_title` records the original title)

### 10.5 API Compatibility

- `Pipeline.process_many()` / `process_tree()` always return `list[PipelineResult]`
- `Task.results: list[PipelineResult]`; API `GET /tasks/{id}/results` returns the multi-document list, `GET /tasks/{id}/result` returns the first item (backward compatible)

## 11. Related Documents

- [Data Models](data-models.md)
- [OCR Layer](ocr.md)
- [LLM Layer](llm.md)
- [API Layer](api.md)
