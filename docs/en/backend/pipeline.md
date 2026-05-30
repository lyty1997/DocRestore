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

> Historical note: Earlier coordinate- / text-feature-based clustering, and the later LLM document boundary detection (`DOC_BOUNDARY`), have both been removed (see the §9.4 historical note). Now "one leaf directory = one document": `process_many()` returns a single `PipelineResult`, and `process_tree()` returns one per leaf directory aggregated into a `list[PipelineResult]`.

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

**Calling conventions**:

- You must call `initialize()` before `process_tree()` / `process_many()`; call `shutdown()` after tasks complete to release resources.
- `process_tree()` is the unified entry point: it automatically detects single/multi-subdirectory structures and ultimately delegates to `process_many()`, returning `list[PipelineResult]` (one per leaf directory; length 1 for a single directory). For multiple subdirectories it first warms up the cold start with the longest directory, then runs the rest concurrently via `asyncio.gather` (see §9.3).
- `process_many()` **treats one directory as one document** and returns a **single** `PipelineResult`; it does no LLM document-clustering split. Internally it runs concurrently as an "OCR producer + streaming consumer" (see §5).
- `controller`: a `RateController` instance. The `process_tree` parallel branch creates one **shared** controller and passes it into each `process_many`, so cold-start sampling and adaptive segment length are reused across subdirectories; when `None`, `process_many` creates one internally on the fly.
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
| `processing/dedup.py` | `IncrementalMerger` (streaming per-page incremental merge) |
| `processing/segmenter.py` | `StreamSegmentExtractor` (streaming incremental segmentation) |
| `pipeline/rate_controller.py` | `RateController` (runtime-adaptive segment length L*, OCR/LLM rate pacing) |
| `llm/base.py` | `LLMRefiner` Protocol |
| `llm/cloud.py` | `CloudLLMRefiner` (cloud implementation: refine/fill_gap/final_refine + PII entity detection) |
| `llm/local.py` | `LocalLLMRefiner` (local implementation: refine/fill_gap/final_refine) |
| `privacy/patterns.py` | Structured PII regexes (phone/email/ID/bank card, etc.) |
| `privacy/redactor.py` | `PIIRedactor` (regex redaction + (optional) cloud entity detection + redaction records) |
| `output/renderer.py` | `Renderer` (renders and writes the final `document.md`) |

## 5. Orchestration Flow Diagram (Streaming: OCR Producer ∥ Streaming Consumer)

`process_many()` (implemented as `_stream_pipeline`) launches two concurrent coroutines: OCR produces
while the LLM consumes, with `RateController` adapting the segment length L* at runtime. OCR is
serialized under `gpu_lock`; while the LLM engine performs refinement the GPU is free, so OCR can keep
advancing to the next page — this is the core benefit of streaming over batch.

```
Pipeline.process_many(image_dir, output_dir, ...) -> PipelineResult (single document)
    |
    |-- scan_images(image_dir) -> list[Path]
    |-- controller = controller or RateController(llm)   # process_tree passes a shared instance
    |-- page_queue: asyncio.Queue[PageOCR | None]
    |-- subscribe to the circuit-breaker OPEN event -> llm_unavailable progress frame (unsubscribe in finally)
    |
    |--+-- (1) OCR producer _ocr_producer (asyncio.Task)
    |  |    for each image:
    |  |      async with gpu_lock: page = engine.ocr(image, output_dir)
    |  |      cleaner.clean(page) -> quality detection -> optional per-page redact_regex_only
    |  |      controller.record_ocr(dt, chars); page_queue.put(page)
    |  |      debug: {stem}_cleaned.md
    |  |    finally: page_queue.put(None)   # sentinel, emitted even on the exception path
    |  |
    |  +-- (2) streaming consumer _stream_process
    |       merger = IncrementalMerger; extractor = StreamSegmentExtractor
    |       while (page := await page_queue.get()) is not None:
    |         merger.add_page(page)                       # per-page incremental merge/dedup
    |         if pii.enable and merger.page_count >= 5 and not done yet:
    |           entity_lexicon = _delayed_pii_detect(...)  # fetch LLM lexicon once enough pages accumulate
    |         _try_extract_and_refine(...)                 # cut segments by controller.target(L*)
    |           -> extractor.try_extract -> refiner.refine (skip on LLMCache hit; fall back to original on failure)
    |           -> accumulate refined_results / all_gaps; controller.record_llm feeds pacing
    |       after sentinel: extract_remaining handles the tail segment
    |       debug: merged_raw.md, rate_controller.json
    |
    +-- (3) finalize _finalize_single_doc (after all segments collected)
         reassemble(refined_results) -> doc            debug: reassembled.md
         -> gap fill (optional enable_gap_fill): re-OCR(gpu_lock)+fill_gap, per-gap exception degradation
         -> final refine (optional enable_final_refine): falls back on failure; duplicate H2 triggers one hinted redo
         -> programmatic fallback (zero LLM cost): dedup_html_tables / dedup_h2_sections
                                    / strip_code_block_line_numbers / strip_residual_ui_noise
         -> parse_gaps collects residual GAPs -> renderer.render -> document.md
         -> aggregate warnings + extract_first_heading(doc_title) -> PipelineResult
```

Notes:

- **Producer/consumer decoupling**: `page_queue` is the back-pressure channel; `controller.set_queue_depth(qsize)` feeds the pacer.
- **PII streaming strategy**: during the OCR production stage, per-page `redact_regex_only` runs first; after `_PII_DETECT_THRESHOLD` (5) pages accumulate, an `EntityLexicon` is fetched once asynchronously for reuse on gap-fill re-OCR fragments (see [privacy.md](privacy.md)).
- **Debug intermediate artifacts**: Used for diagnosing differences across stages; filenames are implementation-defined (`{stem}_cleaned.md` / `merged_raw.md` / `reassembled.md` / `rate_controller.json`, etc.).
- **Truncation detection**: Identifies the risk of LLM output being truncated by length limits and surfaces it as warnings; does not interrupt the pipeline. For threshold details, see [llm.md Section 6](llm.md#6-truncation-detection).
- **Code mode**: when `CodeRestoreConfig.enable=True`, the consumer is replaced by `_code_pipeline`, which does no streaming refinement (see §10).

## 6. Programming Interface Example

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
# result: PipelineResult (one directory is one document)
# result.output_path          -- .md file path
# result.markdown             -- markdown content
# result.warnings             -- pipeline warnings (including truncation detection, etc.)
# result.redaction_records    -- PII redaction statistics (if enabled)

await pipeline.shutdown()
```

> For multi-subdirectory input, use `process_tree()` instead -- it calls `process_many()` per leaf directory and aggregates each single `PipelineResult` into a `list[PipelineResult]` (one per leaf).

## 7. `_reassemble()` Concatenation Algorithm

```
_reassemble(refined_results: list[RefinedResult], merged_doc: MergedDocument) -> MergedDocument:
    1. Take each segment's refined_result.markdown
    2. Join all segments with "\n"
    3. Replace merged_doc.markdown with the joined result, preserving images and gaps
```

The LLM is responsible for deduplicating inter-segment overlaps during refinement; `_reassemble()` only performs simple concatenation. In the streaming version, this algorithm is invoked inside `_finalize_single_doc()` after all segments have been consumed from the queue (followed by gap fill -> final refine -> programmatic fallback -> render).

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
  (`refine` / `fill_gap` / `final_refine` / `detect_pii_entities`);
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

### 9.3 Subdirectory Parallelism (process_tree)

When `process_tree` finds multiple leaf subdirectories under `image_dir`, it dispatches
`process_many` for each subdir via `asyncio.gather` instead of a serial for-loop. Each
subdirectory still runs the full OCR → PII → LLM → render pipeline. The effective
concurrency is shaped by the underlying locks:

- **OCR**: serialized by `gpu_lock` (peak concurrency ≤ 1), preventing GPU OOM;
- **LLM**: throttled by `llm_semaphore` (default 3); refine / gap_fill across
  subdirectories can execute concurrently;
- **PII / dedup / reassemble / render**: pure CPU/IO, fully parallel.

As a result, once subdir 1 enters its LLM stage (and has released `gpu_lock`), subdir 2
can start OCR immediately, avoiding the "GPU idle while LLM runs" gap. If any subdir
raises, `asyncio.gather` fails fast and the upstream `TaskManager` marks the task as
FAILED (same semantics as the serial version).

> Test: `tests/pipeline/test_process_tree.py` covers the single/multi-subdirectory entry point and the parallel branch.

### 9.4 No Group-level Concurrency

Clustering has been removed -- all images are treated as a single document, so there is no "group-level concurrency" or "split-by-group task" scheduling logic. All concurrency strategies are bounded at the task level.

> **History**: there once was an "LLM document clustering" design — the refinement stage detected `DOC_BOUNDARY` markers to split the merged text into multiple sub-documents. That path, along with the `parse_doc_boundaries` / `detect_doc_boundaries` / `DocBoundary` symbols, was fully removed on 2026-05-29 (code mode uses its own `group_into_files` aggregation and never reused DOC_BOUNDARY; doc mode no longer uses it either). Now "one leaf directory = one document", and `process_many()` returns only a single `PipelineResult`. For the origin of this design reversal see [references/streaming-pipeline.md](references/streaming-pipeline.md).

## 10. Code Mode Orchestration (`CodeRestoreConfig.enable=True`)

When `PipelineConfig.code.enable=True`, after launching the OCR producer `_stream_pipeline` picks one of two branches based on `code_cfg.enable`: the consumer is replaced by the dedicated code-mode branch `_code_pipeline` (instead of `_stream_process`), skipping the plain-mode streaming refine / incremental merge / segmentation chain.

### 10.1 OCR Engine Forced to `basic`

Code mode forces the OCR engine to PaddleOCR's `basic` pipeline (PP-OCRv5), because only `basic` produces line-level `text_lines` (with bbox + text) and code-column assembly depends on that input; the VL pipeline does not emit `text_lines`, so enabling code mode would fail with nothing to assemble. Request-level `ocr` overrides are rewritten through `_ocr_config_for_code_mode` so the check is centralized rather than duplicated at every call site (B4 H5).

### 10.2 Chain (Runs Sequentially After OCR Drains)

```
Per image: analyze_layout → [secondary_column_ocr*] → extract_ide_metas → assemble_columns
                          → build PageColumn[]
Cross images: group_into_files → SourceFile[]
Post-OCR: clean_code_ocr_text (conservative character-level fixes, line-count preserved,
          before PII / LLM)
PII: _redact_code_headers (only the leading comment block of each file)
Diagnose: diagnose_source_files → pre-refine diagnostics
LLM refine (per SourceFile, sequential):
  ├─ syntax_dirty       → DiagnosticCodeRepairer.repair → re-diagnose → CodeConsistencyAuditor.audit
  ├─ Large file > threshold → skipped, flag code.repair.skipped_large_file_no_window
  └─ Otherwise          → CodeLLMRefiner.refine (mode=refine|rewrite)
Render: render_code_files → output_dir/files/<relative-path> + files-index.json
Quality: detect_code_mode_quality → .quality_report.json
```

`*` runs only when `code_cfg.secondary_column_ocr=True` — each detected column is cropped, enhanced, and re-OCR'd (off by default).

### 10.3 Error Handling

- **Image with no `text_lines`**: the page is skipped and added to `missing_line_pages` (reported upstream); other pages continue.
- **No columns produced at all**: `raise RuntimeError("代码模式：OCR producer 未产出任何页")`, caught at the task layer and written as an error result.
- **Per-`SourceFile` LLM refine / repair / audit failure**: `catch Exception`, fall back to the original text, log + write a quality flag, do not interrupt other files in the same task.
- **PII failure** (cloud entity detection error): same policy as plain mode — degrade per `SourceFile`.
- **Missing external diagnostic tool**: `CodeDiagnosticRunner` degrades to `tool_unavailable` rather than failing the task (see [processing.md §3.5](processing.md)).

### 10.4 Concurrency and Resources

- The OCR producer and `_code_pipeline` are decoupled through `page_queue`; OCR is serialized by `gpu_lock`, and `_code_pipeline` runs the downstream stages sequentially once the OCR queue drains.
- LLM refine / repair / audit run **sequentially per file**, not concurrently (avoid firing many long-context requests at the LLM provider simultaneously and triggering rate limits; the number of `SourceFile`s is typically ≤ a few dozen, sequential is manageable).
- Blocking IO (`diagnose_source_files`, the rglob / read_text inside `build_repair_contexts`) is dispatched via `asyncio.to_thread` to keep the event loop responsive (B7 C12 / S3).

### 10.5 Output and Compatibility

`_code_pipeline` returns `PipelineResult(output_path=document.md, markdown="")`:
- `output_path` points to `output_dir/document.md` (placeholder, kept for the legacy UI route)
- `markdown` is empty; the frontend renders the code-mode review view from `files-index.json` (see [frontend/features.md §7](../frontend/features.md))
- `warnings` carries a `code_mode: N files, M skipped` summary

## 11. Related Documents

- [Data Models](data-models.md)
- [OCR Layer](ocr.md)
- [LLM Layer](llm.md)
- [API Layer](api.md)
