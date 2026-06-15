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

# Pipeline-Level Parallelism Design (Multi-Task Parallel Pipelines)

> **Status**: Historical design reference. Multi-task pipeline scheduling status is tracked in Linear AGE-71 (hardware-aware multi-task scheduling); the repo currently ships only the single-task streaming pipeline. Retained to record the motivation and constraints.

> Drafted 2026-04-16. Orthogonal to `streaming-pipeline.md` (intra-task OCR↔LLM overlap):
> this design lets **multiple tasks** run pipelines concurrently. The OCR stage is
> serialized by the GPU lock, while non-GPU stages (LLM / PII / render) run
> concurrently across tasks, soaking up the GPU idle cycles in the current
> multi-task scenario.

## 1. Background and Goals

### 1.1 Problem

Today, `TaskManager` already calls `asyncio.create_task(run_task)` on `POST /tasks`,
so multiple tasks are supposed to run concurrently. In reality, however:

- The `scheduler._pipeline_semaphore` corresponding to
  `QueueConfig.max_concurrent_pipelines = 3` is **never acquired anywhere in the
  code** (`scheduler.py:32` creates it → no global call site).
- In single-task benchmarks, DeepSeek-OCR-2 with batch=4 hits 0.56 img/s
  throughput, GPU p95 81%, but the GPU sits idle during the **non-GPU stages
  (PII detection / LLM segment refine / gap fill / final refine)**.
- Cloud LLMs typically take >30s per segment; the LLM stage of a single pipeline
  is usually as long as or longer than the OCR stage.

→ In the multi-task scenario (a user submits several directories at once), the
  current behavior is "concurrent start but serial wait on GPU, no concurrency
  protection on the LLM stage". We neither soak up GPU concurrency nor avoid
  blowing up the LLM API with bursty concurrency that gets throttled.

### 1.2 Goals

| Dimension | Current | Target |
|---|---|---|
| GPU utilization | Single-task p95 81%, mean 52% | Multi-task GPU mean ≥ 70% |
| End-to-end throughput (2 concurrent tasks) | ≈ 2 × single-task time (serial wait on OCR) | ≈ 1 × single-task time + LLM time |
| LLM API stability | Unthrottled, bursty concurrency blows up the relay | Globally ≤ `max_concurrent_requests` |
| Observability | Single-task profile.json | Per-task independent profile, supports comparison analysis |

### 1.3 Engineering Assessment

**Just right**:
- No new components introduced, no Pipeline data flow changes.
- Activates the already-existing `scheduler._pipeline_semaphore` (in fact replaced
  by `llm_semaphore`, see §3).
- Touch surface: a few `async with` in `scheduler.py` and `pipeline.py`, plus 2
  new fields in `config.py`.
- Estimated < 200 lines of changes + ~150 lines of tests.

**Not in scope**:
- Intra-task OCR↔LLM streaming overlap (streaming-pipeline.md, deferred to the
  super-large-PDF scenario).
- Pipeline instance pool (current singleton + asyncio concurrency is enough).
- Cross-process / distributed scheduling (single-machine single-process with 3
  tasks is the upper bound of current demand).

## 2. Current State Snapshot

### 2.1 Key Code Anchors

| Concern | File:line | Current behavior |
|---|---|---|
| Task starts immediately after creation | `api/routes.py:339` | `asyncio.create_task(manager.run_task(task_id))` — no wait, no throttle |
| Global singleton Pipeline | `api/app.py:174` | `app.state.pipeline = Pipeline(config)` |
| Scheduler creation | `api/app.py:~180` | `PipelineScheduler(max_concurrent_pipelines=3)` |
| GPU lock | `scheduler.py:31` | Global `asyncio.Lock()`, injected into EngineManager / Pipeline |
| Semaphore | `scheduler.py:32` | Created but `.acquire()` is **never called** |
| Pipeline entry | `task_manager.py:242` | `pipeline.process_tree(gpu_lock=scheduler.gpu_lock, ...)` |
| LLM call sites | scattered across `pipeline.py` | Segment refine / gap fill / final refine / doc boundary detection — **all unthrottled** |

### 2.2 Current Concurrency Semantics

```
POST /tasks A     POST /tasks B     POST /tasks C
     │                │                │
     ├ create_task ───┤ create_task ───┤ create_task
     ↓                ↓                ↓
  run_task(A)     run_task(B)      run_task(C)
     │                │                │
     ├── OCR ─┐       ├── OCR ─┐       ├── OCR ─┐
     │  gpu_lock ═════════════════════════════ shared lock, serialized
     ↓        │       ↓        │       ↓        │
  [PII]   [waits for A]     [waits for B]
     │        ↓        │        ↓        │        ↓
  [LLM×N]  [PII]     [LLM×N]  [PII]   [LLM×N]
     │        │        │        │        │
     └── all LLM requests fired concurrently, 3×N segments in flight → overloads the relay
```

### 2.3 Benchmark Extrapolation (2-Task Scenario)

Single task DeepSeek-OCR-2 36 images + LLM refine ≈ 180s (OCR 65s + PII 15s +
LLM 100s). Two concurrent tasks under current semantics:
- Two OCR runs serialized: 130s
- LLM overlaps but is unconstrained: ~100s
- Total: ≈ 230s (= 2 × 180 − 100 overlap)
- Ideal (LLM constrained to 2 concurrent): ≈ 250s, but the LLM API does not
  get blown up.

## 3. Design Decisions

### 3.1 Confirmed Decisions (Conversation 2026-04-16 afternoon)

| # | Decision | Conclusion | Rationale |
|---|---|---|---|
| 1 | Scope | **Multi-task parallelism only**, streaming deferred | Small change, clear payoff, leave streaming for huge documents |
| 2 | Default concurrency | **3** | GPU lock naturally serializes OCR; 3 pipelines simultaneously in the LLM stage are safe for both memory and the API |
| 3 | Semaphore granularity | **Wrap only the LLM/PII non-GPU stages** (option B) | Option A (wrap the whole `run_task`) would block subsequent tasks on the semaphore and waste a slot during the OCR stage |
| 4 | LLM API throttling | **Required**, independent of the pipeline semaphore, default 3 | A pipeline doing segment refine may issue 10+ requests; the pipeline semaphore is too coarse-grained |
| 5 | Profiler | **Per-task independent profile** | ContextVar is already isolated per-asyncio-task; just name the output file by task_id |

### 3.2 Derived Decisions (Added by This Document)

**Decision 6: Drop `pipeline_semaphore`, keep only `llm_semaphore`**

pipeline_semaphore (coarse-grained) and llm_semaphore (fine-grained) — pick one:
- pipeline_semaphore = 3: allows 3 pipelines into the LLM stage simultaneously,
  but each pipeline's internal segment refine of 10 segments fires
  10 × 3 = 30 API requests at once, providing no protection for the API.
- llm_semaphore = 3: at most 3 API requests in flight globally, exactly
  protecting the API and naturally bounding concurrent pipelines (because every
  pipeline's LLM stage is gated by llm_semaphore).

→ **Keep only llm_semaphore**, delete pipeline_semaphore (currently dead code).
  Rename the field `QueueConfig.max_concurrent_pipelines` to
  `LLMConfig.max_concurrent_requests` — the new name reflects the semantics
  more accurately.

**Decision 7: LLM Throttling Scope**

All cloud / local LLM calls:
- `CloudLLMRefiner.refine()` — segment refine
- `CloudLLMRefiner.fill_gap()` — gap filling
- `CloudLLMRefiner.final_refine()` — full-document refine
- `LocalLLMRefiner.*` — same granularity for local LLM

All unified at `_BaseLLMRefiner._build_kwargs` / right before the actual API
call to acquire the semaphore, instead of adding it at every call site.

> Note: the former cloud entity detection `CloudLLMRefiner.detect_pii_entities()`
> was removed in S4 (2026-06-15). Entity detection moved to local NER
> (`PIIGuard.detect_entities` → `privacy/ner.py`, main-process CPU, does not hold
> the llm_semaphore), so the llm_semaphore now only constrains
> refine / fill_gap / final_refine.

**Decision 8: How Semaphores Are Injected**

Inject into the Refiner via constructor parameters; **do not use ContextVar**:
- The Refiner is created in `Pipeline._create_refiner()`, which can pass
  `scheduler.llm_semaphore` directly.
- ContextVar fits "deeply nested call stacks pick it up automatically", but the
  Refiner construction site already has visibility into the scheduler.
- Constructor injection is easier to test (mocks don't need to set a ContextVar).

**Decision 9: PII Redaction Stage Is Not Throttled**

PII redaction has two phases:
- Regex (`patterns.py`) — pure CPU, no network, no throttling needed.
- Entity detection (local NER `PIIGuard.detect_entities` → `privacy/ner.py`) —
  main-process CPU, no network, no throttling needed.

→ Both phases are pass-through (`_replace_custom_words` / regex matching / local
  NER entity detection); the llm_semaphore does not constrain PII redaction.

> Note: before S4 (2026-06-15), entity detection went through the cloud
> `detect_pii_entities()` (an LLM call that held the llm_semaphore). That cloud
> call has been removed and entity detection moved to local NER (names never
> leave the machine), so the entire PII redaction stage no longer touches the
> llm_semaphore.

**Decision 10: profile.json Naming**

- Current: `{output_dir}/profile.json`
- Multi-task concurrent: `{output_dir}/profile.json` (each task has its own
  output_dir, naturally isolated)

→ The path is already naturally unique (each Task has a different output_dir),
  so **no rename is needed**. `PipelineConfig.profiling_output_path` retains
  the ability to "manually specify an absolute path", but in the multi-task
  concurrent scenario it is recommended to **leave it empty** so it lands in
  output_dir.

## 4. Architectural Changes

### 4.1 Change List

| File | Change type | Description |
|---|---|---|
| `pipeline/config.py` | Modify | Remove `QueueConfig.max_concurrent_pipelines` → add `LLMConfig.max_concurrent_requests` |
| `pipeline/scheduler.py` | Modify | Remove `pipeline_semaphore` → add `llm_semaphore` (built from `LLMConfig.max_concurrent_requests`) |
| `api/app.py` | Modify | When creating Scheduler, pass `config.llm.max_concurrent_requests` |
| `pipeline/pipeline.py` | Modify | `_create_refiner()` passes `scheduler.llm_semaphore` to the Refiner constructor |
| `llm/cloud.py` | Modify | `_BaseLLMRefiner.__init__` accepts `llm_semaphore: asyncio.Semaphore \| None`, `async with` before each API call |
| `llm/local.py` | — | Inherits from `_BaseLLMRefiner`, gets throttling automatically |
| `pipeline/task_manager.py` | No change | `run_task` semantics unchanged; throttling happens inside the Pipeline |
| `tests/pipeline/` | New | `test_pipeline_parallel.py`: 2-task concurrency + llm_semaphore validation |

### 4.2 Before / After

**scheduler.py**

```diff
 class PipelineScheduler:
-    def __init__(self, max_concurrent_pipelines: int = 3) -> None:
+    def __init__(self, max_concurrent_llm_requests: int = 3) -> None:
         self._gpu_lock = asyncio.Lock()
-        self._pipeline_semaphore = asyncio.Semaphore(max_concurrent_pipelines)
+        self._llm_semaphore = asyncio.Semaphore(max_concurrent_llm_requests)

     @property
     def gpu_lock(self) -> asyncio.Lock: ...

-    @property
-    def pipeline_semaphore(self) -> asyncio.Semaphore: ...
+    @property
+    def llm_semaphore(self) -> asyncio.Semaphore: ...
```

**llm/cloud.py** (base class snippet)

```diff
 class _BaseLLMRefiner:
-    def __init__(self, config: LLMConfig) -> None:
+    def __init__(
+        self,
+        config: LLMConfig,
+        llm_semaphore: asyncio.Semaphore | None = None,
+    ) -> None:
         self._config = config
+        self._llm_semaphore = llm_semaphore

     async def _call_llm(self, messages: list[dict[str, str]]) -> str:
-        response = await litellm.acompletion(**kwargs)
+        if self._llm_semaphore is None:
+            response = await litellm.acompletion(**kwargs)
+        else:
+            async with self._llm_semaphore:
+                response = await litellm.acompletion(**kwargs)
         return response.choices[0].message.content or ""
```

> Note: today `cloud.py` calls `litellm.acompletion` from multiple places. As
> part of the refactor, first extract a unified `_call_llm()` entry point, then
> add the semaphore at that entry (avoid maintaining throttling at multiple sites).

**pipeline/pipeline.py**

```diff
 class Pipeline:
     def __init__(
         self,
         config: PipelineConfig,
+        scheduler: PipelineScheduler | None = None,
     ) -> None:
         ...
+        self._scheduler = scheduler

-    def _create_refiner(self, llm_config: LLMConfig) -> LLMRefiner:
+    def _create_refiner(self, llm_config: LLMConfig) -> LLMRefiner:
         if llm_config.provider == "cloud":
             from .llm.cloud import CloudLLMRefiner
-            return CloudLLMRefiner(llm_config)
+            llm_sem = self._scheduler.llm_semaphore if self._scheduler else None
+            return CloudLLMRefiner(llm_config, llm_semaphore=llm_sem)
         ...
```

### 4.3 Concurrency Timing (Target State)

```
POST /tasks A         POST /tasks B         POST /tasks C
     │                     │                     │
     ├ create_task ────────┤ create_task ────────┤ create_task
     ↓                     ↓                     ↓
  run_task(A)           run_task(B)           run_task(C)
     │                     │                     │
  [OCR A ══ gpu_lock]   [waits for A]         [waits for B]
     │                     ↓                     │
  [PII regex]           [OCR B ══ gpu_lock]      ↓
     │                     │                  [waits for B]
  [LLM seg1 ──sem──]    [PII regex]           [OCR C ══ gpu_lock]
     │                     │                     │
  [LLM seg2 ──sem──]    [LLM seg1 ──sem──]    [PII regex]
     │                     │                     │
  [gap fill ──sem──]    [LLM seg2 ──sem──]    [LLM seg1 ──sem──]
     │                     │                     │
  [final refine ─sem─]  [gap fill ──sem──]    ...
     │                     │                     
  [render + profile]    [final refine ─sem─]     
                           │
                        [render + profile]
```

- OCR stage: serialized by gpu_lock (A → B → C, same as today).
- LLM stage: llm_semaphore=3 lets 3 requests fly simultaneously; the 4th queues up.
- Each task's profile.json lands independently in its own output_dir.

## 5. Configuration Fields

### 5.1 Added

```python
# pipeline/config.py::LLMConfig
class LLMConfig(BaseModel):
    ...
    # 全局 LLM API 并发上限（跨所有活跃 pipeline）
    # 默认 3：与 max_concurrent_pipelines 保持一致的直觉值
    # 云端中转站多数限流 5-10 RPS，3 并发 × ~30s/req ≈ 0.1 RPS，留足余量
    max_concurrent_requests: int = 3
```

### 5.2 Removed / Migrated

```python
# pipeline/config.py::QueueConfig — 整个类废弃
# 因为 max_concurrent_pipelines 是该类唯一字段，且当前无其他使用点
class QueueConfig(BaseModel):
    max_concurrent_pipelines: int = 3  # ← 删除
```

The `PipelineConfig.queue: QueueConfig` field is removed alongside it.
If the API layer's `CreateTaskRequest` exposed this field (not expected to),
it must be cleaned up in sync.

### 5.3 Compatibility

- Is `QueueConfig` referenced by the frontend or by API requests?
  - grep `max_concurrent_pipelines` / `QueueConfig` across the whole repo;
    expected to only be referenced by `config.py`, `scheduler.py`, and test
    fixtures.
  - If the frontend references it → emit a deprecation warning for one minor
    release before submitting, for compatibility.
- Is the field persisted anywhere?
  - `PipelineConfig` is written to the DB via `model_dump_json()` (the Task
    table's config column).
  - For old Task records that contain a `queue` field: pydantic
    `model_validate_json()` defaults to `extra="ignore"` for unknown fields
    (current model_config needs to be confirmed); loading old data won't error.

## 6. Concurrency Semantics and Boundaries

### 6.1 GPU Lock × LLM Semaphore Interaction

The two synchronization primitives are **independent of each other**:
- GPU lock: acquired during the OCR stage; once released, the task can move on
  to the LLM stage.
- LLM semaphore: acquired before an LLM call, released after a single call
  completes.

There is no deadlock: a task holding the GPU lock will not acquire the LLM
semaphore (no LLM calls during the OCR stage), and vice versa. The gap fill
path is special (LLM detects gap → re-OCR needs GPU):
- First `async with llm_semaphore: detect gap` (holds the LLM sem).
- Release the LLM sem.
- `async with gpu_lock: reocr_page` (holds the GPU lock).
- Release the GPU lock.
- `async with llm_semaphore: fill_gap(reocr_text)`.
→ The two locks are held alternately, never nested. Safe.

### 6.2 Task Failure and Cancellation

- Single-task failure: the `try/except` in `run_task` already handles it
  correctly and does not affect other tasks.
- Global shutdown: `asyncio.CancelledError` bubbles up to `run_task` → all
  semaphores are released (`async with` guarantees this).
- `llm_semaphore` is not explicitly closed at app shutdown (Python semaphores
  have no such API) — once tasks are cancelled, the semaphore is naturally
  released.

### 6.3 Back-Pressure

- The LLM semaphore *is* the back-pressure: the 4th request blocks on
  `await semaphore.acquire()` until a previous request returns.
- No unbounded buildup: the Pipeline calls the LLM segment by segment, and the
  next segment is only sent after the previous `await` completes. Memory
  footprint = in-flight segments × segment_chars ≤ 3 × 8000 ≈ 24KB of text +
  response buffer.

### 6.4 Progress Push

- Each task pushes through `TaskManager.publish_progress(task_id, ...)` to its
  own Queue.
- The WS connection `/tasks/{task_id}/progress` subscribes by task_id.
- Multi-task concurrency does not mix up progress pushes (the existing
  mechanism already guarantees this).
- Added: while `await llm_semaphore` is in effect, set `TaskProgress.message`
  to `"Waiting on LLM throttle (n/3)"` so the user knows where it is blocked
  (optional optimization).

### 6.5 Profiler Multi-Task

- `ContextVar` is automatically bound to the `asyncio.Task` → each `run_task`
  gets its own Profiler; events do not cross-contaminate.
- `profile.json` lands at `output_dir/profile.json`, naturally isolated.
- New Profiler stage: `llm.acquire` (timed from `await llm_semaphore.acquire()`
  start to acquired) → makes it easy to quantify time spent waiting on the throttle.

## 7. Implementation Steps

In order of increasing change granularity, each step independently testable:

**Step 1: Config and Scheduler refactor**
1. Add `max_concurrent_requests: int = 3` to `LLMConfig`.
2. Remove `QueueConfig` (and `PipelineConfig.queue`).
3. `scheduler.py`: rename `_pipeline_semaphore` → `_llm_semaphore`, rename the
   constructor parameter.
4. `api/app.py`: `PipelineScheduler(config.llm.max_concurrent_requests)`.
5. Run the existing tests; confirm no import errors.

**Step 2: LLMRefiner injection + throttling**
1. `_BaseLLMRefiner.__init__` accepts `llm_semaphore`.
2. Extract a `_call_llm()` method, funneling all `litellm.acompletion` calls
   through it.
3. At the entry of `_call_llm()`, `async with self._llm_semaphore if not None`.
4. `LocalLLMRefiner` inherits this for free; no changes needed.
5. Write a unit test: mock litellm + assert the number of `semaphore.acquire`
   calls.

**Step 3: Pipeline takes Scheduler injection**
1. `Pipeline.__init__` accepts `scheduler: PipelineScheduler | None`.
2. `_create_refiner()` reads `self._scheduler.llm_semaphore` and passes it to
   the Refiner.
3. `api/app.py` constructs the Pipeline as `Pipeline(config, scheduler=scheduler)`.
4. The legacy code path (no scheduler) falls back to `llm_semaphore=None`; unit
   tests and standalone calls are unaffected.

**Step 4: Multi-task concurrency integration tests**
1. Add `tests/pipeline/test_pipeline_parallel.py`.
2. Mock the OCR engine + mock the LLM, spawn 3 `asyncio.create_task(run_task)`.
3. Assert: the maximum LLM concurrency observed by the mock ≤
   `max_concurrent_requests`.
4. Assert: number of `gpu_lock` acquisitions == per-task OCR call count.
5. Use `asyncio.Event` to simulate slow LLM responses, verify back-pressure.
6. Verify each of the 3 tasks' profile.json is independent and events do not
   cross-contaminate.

**Step 5: Real benchmark**
1. `scripts/bench_pipeline_parallel.py` (new): launch 2-3 concurrent tasks,
   record end-to-end latency + GPU trace.
2. Compare serial (1 task × 3 runs) vs. concurrent (3 tasks at once); validate
   the throughput improvement.
3. Inspect the latency distribution of the `llm.acquire` stage in `profile.json`
   (ideal: most < 100ms, with a long tail of throttled requests).

**Step 6: Documentation and progress sync**
1. Update `docs/zh/backend/pipeline.md`: add a "multi-task parallelism" section
   under the concurrency model chapter.
2. Update `CLAUDE.md` or memory: the `max_concurrent_pipelines` field has been
   removed.
3. `progress.md` 2026-04-XX entry: cover the change list + bench data.

## 8. Test Plan

### 8.1 Unit Tests

| Case | File | Assertion |
|---|---|---|
| `_BaseLLMRefiner` pass-through without semaphore | `tests/llm/test_cloud.py` | When constructed with `llm_semaphore=None`, `_call_llm` does not acquire |
| `_BaseLLMRefiner` throttling with semaphore | `tests/llm/test_cloud.py` | Mock acompletion with slow responses; 3 tasks fire at once → observed concurrency peak ≤ 2 (semaphore=2) |
| Scheduler refactor backward compatibility | `tests/pipeline/test_scheduler.py` | Construct `PipelineScheduler(max_concurrent_llm_requests=N)` → `scheduler.llm_semaphore._value == N` |
| Pipeline fallback without scheduler | `tests/pipeline/test_pipeline.py` | `Pipeline(config, scheduler=None)` runs end-to-end (fixture OCR + fixture LLM) |

### 8.2 Integration Tests

- `test_pipeline_parallel.py`: 3 tasks × mock OCR × mock LLM
  - Test 1: Concurrent task cap — spawn 5 tasks at once, verify LLM concurrency
    peak = 3.
  - Test 2: Failure isolation — make task 2 raise; verify tasks 1/3 complete
    normally.
  - Test 3: Cancellation isolation — cancel task 2; verify tasks 1/3 are
    unaffected.
  - Test 4: Profiler isolation — each of the 3 tasks' output_dir contains its
    own profile.json.

### 8.3 Benchmark Regression

- `scripts/bench_pipeline_parallel.py`:
  - Baseline: 1 task serial × 3 runs.
  - Target state: 3 tasks concurrent × 1 run.
  - Metrics: total latency, GPU utilization mean, LLM API request concurrency
    peak.
- Pass criterion: concurrent latency ≤ 0.6 × serial latency (payoff ≥ 40%).

## 9. Risks and Rollback

### 9.1 Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| SQLite multi-task concurrent write `database is locked` | Medium | Medium | aiosqlite + WAL is already enabled; current concurrency 3 won't trigger it; if it does, add a retry decorator |
| Memory blowup (intermediate text from 3 tasks accumulates) | Low | Medium | Single-task text < 10MB, 3 copies < 30MB, far smaller than the current resident Python process |
| LLM throttling unfair (a large task starves a small one) | Low | Low | FIFO acquire is already fair; strict FIFO would need `asyncio.BoundedSemaphore` (optional upgrade) |
| User configures `max_concurrent_requests=1`, degrades to serial | — | — | This is the intended behavior, serves as a fallback switch |
| gap fill's LLM → GPU → LLM three-phase release ordering goes wrong | Low | High (deadlock) | §6.1 explicitly states no nested holding; covered by an integration test |

### 9.2 Rollback Plan

Changes are concentrated in 4 files, all via git:
1. Revert the `feat(core): pipeline-level parallelism ...` commit.
2. Verify the single-task Pipeline path still works.
3. If the frontend does not consume `max_concurrent_requests`, no changes are
   needed there.

No "feature flag" — the change is small and well-tested, just ship it; if real
problems show up, `git revert`.

### 9.3 Deprecation

- Removing `QueueConfig` affects old Task records that have been serialized.
- pydantic's default `extra="ignore"` is compatible with old JSON.
- If the current `PipelineConfig`'s `model_config` is `extra="forbid"`, the
  implementation commit must first relax it for one transitional release, then
  remove the field in the next release.

## 10. Open Items and Future Work

- **Streaming Pipeline** (intra-task OCR↔LLM overlap):
  see `streaming-pipeline.md`. Tackle this when single-task latency in the
  super-large PDF or code-photo scenario becomes the pain point.
- **Pipeline-level parallelism + Streaming stacked**: the two are orthogonal
  and can be enabled together later (multi-task parallelism + intra-task
  streaming). At that point, llm_semaphore remains the correct global
  throttling point.
- **PaddleOCR ocr_batch** (Task #13): currently PaddleOCR runs serial-per-image
  through the base class and cannot enjoy the batch=4 dividend. This design is
  orthogonal to Paddle batch; once Paddle batch lands, multi-task parallelism
  will inherit the gains automatically.
- **API-layer request queueing**: today `POST /tasks` immediately
  `create_task` with no queueing; if future concurrency exceeds
  `max_concurrent_requests × 3`, add a task queue at the API layer
  (`asyncio.Queue` + worker pool); not needed for now.
- **Distributed scheduling**: single-machine single-process is sufficient; if
  cross-machine becomes necessary, consider Celery/Ray, beyond the scope of
  this design.

## Appendix A: Key File Change Estimates

| File | +LOC | -LOC | Net change |
|---|---:|---:|---:|
| `pipeline/config.py` | +5 | -8 | -3 |
| `pipeline/scheduler.py` | +6 | -6 | 0 |
| `api/app.py` | +1 | -1 | 0 |
| `pipeline/pipeline.py` | +8 | 0 | +8 |
| `llm/cloud.py` | +20 | -5 | +15 |
| `tests/pipeline/test_pipeline_parallel.py` | +150 | 0 | +150 |
| `scripts/bench_pipeline_parallel.py` | +120 | 0 | +120 |
| `docs/zh/backend/pipeline.md` | +30 | -10 | +20 |
| **Total** | **+340** | **-30** | **+310** |

Core logic changes < 50 lines; the rest is tests and benchmarks.
