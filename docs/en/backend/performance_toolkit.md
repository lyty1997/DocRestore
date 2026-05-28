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

# DocRestore Performance Observability and Throughput Tuning Toolchain

Status: Draft (2026-04-16)
Scope: Plan 1 (OCR batch + pipelining) + GPU Monitor + end-to-end Pipeline Profiler instrumentation

## 1. Background and Goals

Conclusions from the 2026-04-16 vLLM tuning baseline comparison (see [`../../progress.archive.md`](../../progress.archive.md)):

- Generic vLLM parameters bring no steady-state throughput gains for either OCR engine
- GPU utilization is low: PaddleOCR mean 20% / p95 66%; DeepSeek 52% / p95 73%
- `enforce_eager=True` actually regresses PaddleOCR by -70% (CUDA Graph is disabled)

The next optimization direction is **raising GPU utilization**. This document covers three pieces of work, each delivered behind an independent toggle:

| Module | What it solves | Default |
|---|---|---|
| OCR batch + in-worker GPU↔CPU pipelining | Pipeline serial gaps + GPU/CPU stages cannot overlap | On (K=4) |
| GPU Monitor | Lack of observability and fallback for VRAM fragmentation and OOM | On (lightweight sampling) |
| Pipeline Profiler instrumentation | Per-stage end-to-end latency share is unclear | **Off** (enable for debugging) |

Non-goal: Pipeline-level async pipelining (the original Plan 3, orthogonal to this work, to be pursued separately later).

## 2. Overall Data Flow

```
┌──────────────────────────────────────────────────────────────────┐
│ Pipeline.process()                                              │
│   with profiler.stage("pipeline.total"):                        │
│     with profiler.stage("ocr.phase"):                           │
│       async for batch in chunks(imgs, K):       ◀── Plan 1       │
│         async with gpu_lock:                                    │
│           pages = await engine.ocr_batch(batch) ──▶┐            │
│     with profiler.stage("cleaner.page"): ...       │            │
│     ... downstream stages (dedup/pii/llm/render) all instrumented│
└────────────────────────────────────────────────────┼────────────┘
                                                     ▼
                ┌──────────────────────────────────────────────────┐
                │ DeepSeek worker (separate conda process)         │
                │                                                  │
                │  cmd:"ocr_batch" {image_paths:[...]}             │
                │    ↓                                             │
                │  asyncio.gather(*[_process_one(img) for img])    │
                │    ├─ vLLM.generate()  ─┐                        │
                │    │                    │ continuous batching    │
                │    │   (GPU batching)   │                        │
                │    ├─ grounding parse   │                        │
                │    ├─ image cropping    │  (CPU postproc and the │
                │    └─ disk write        │   next GPU step overlap│
                │    → profile{gpu_ms,cpu_ms,parse_ms,write_ms}    │   naturally)
                │                                                  │
                │  [background task] gpu_monitor                   │
                │    samples mem_get_info + allocated/reserved 1Hz │
                │    free < margin → empty_cache + WARN            │
                │                                                  │
                │  OOM catch → retry at K/2 → raise if K=1 fails   │
                └──────────────────────────────────────────────────┘
```

## 3. OCR batch + Pipelining

### 3.1 Protocol Extension

The worker JSON Lines protocol gains a new command `ocr_batch`:

**Request**
```json
{
  "cmd": "ocr_batch",
  "image_paths": ["/abs/path/A.jpg", "/abs/path/B.jpg", ...],
  "output_dir": "/abs/path/output",
  "enable_column_filter": false,
  "column_filter_min_sidebar": 5
}
```

**Response**
```json
{
  "ok": true,
  "results": [
    {
      "ok": true,
      "image_path": "/abs/path/A.jpg",
      "ocr_dir": "/abs/path/output/A_OCR",
      "raw_text": "...",
      "image_size": [W, H],
      "has_eos": true,
      "regions": [...],
      "profile": {"gpu_ms": 1840, "cpu_ms": 210, "parse_ms": 35, "write_ms": 60}
    },
    {"ok": false, "image_path": "/abs/path/B.jpg", "error": "..."},
    ...
  ]
}
```

A single-image failure does not block the others (gather with `return_exceptions=True`); results are returned in the order of `image_paths`.

### 3.2 In-Worker Concurrency Model

```python
async def handle_ocr_batch(req: dict) -> dict:
    imgs = [Path(p) for p in req["image_paths"]]
    output_dir = Path(req["output_dir"])

    async def _process_one(img: Path) -> dict:
        t0 = time.monotonic()
        # GPU stage — vLLM async generate; concurrent coroutines trigger continuous batching automatically
        final = None
        async for out in engine.generate(prompt, sampling_params, request_id=...):
            final = out
        t_gpu = time.monotonic()
        # CPU stage — grounding parse + cropping + disk write (to_thread releases the event loop)
        result = await asyncio.to_thread(postprocess, final, img, output_dir)
        t_cpu = time.monotonic()
        result["profile"] = {
            "gpu_ms": int((t_gpu - t0) * 1000),
            "cpu_ms": int((t_cpu - t_gpu) * 1000),
            ...
        }
        return result

    results = await asyncio.gather(
        *[_process_one(img) for img in imgs],
        return_exceptions=True,
    )
    # 异常转 {ok: False, error: str(exc)}
    return {"ok": True, "results": [_normalize(r) for r in results]}
```

### 3.3 Pipeline-Side Invocation

```python
# pipeline/pipeline.py 主循环
batch_size = self._config.ocr.ocr_batch_size
if batch_size < 2:
    # 回退：逐张处理（保留现有路径）
    for img in images:
        async with gpu_lock:
            page = await engine.ocr(img, output_dir)
        ...
else:
    for batch in _chunks(images, batch_size):
        async with gpu_lock:
            with self._profiler.stage("ocr.batch", batch_size=len(batch)):
                pages = await engine.ocr_batch(batch, output_dir)
        for page in pages:
            await cleaner.clean(page)
            ...
```

### 3.4 Incremental OCR Compatibility

Before each image enters `_process_one`, check whether `{stem}_OCR/result.mmd` already exists; if so, return cached. If every image is cached, the worker does not need to load vLLM at all.

### 3.5 PaddleOCR Side (Deferred)

ppocr-server already supports concurrent requests; the bottleneck is only the per-image HTTP in `scripts/paddle_ocr_worker.py`. The change: add an `ocr_batch` command that dispatches multiple HTTP requests via `asyncio.gather`. Will be done after the DeepSeek side validates the gains.

## 4. GPU Monitor

### 4.1 In-Process Monitor Inside the DeepSeek Worker

```python
# scripts/deepseek_ocr_worker.py
async def _gpu_monitor(interval_s: float, safety_margin_bytes: int,
                       stop: asyncio.Event) -> None:
    import torch
    while not stop.is_set():
        free, total = torch.cuda.mem_get_info()
        alloc = torch.cuda.memory_allocated()
        reserved = torch.cuda.memory_reserved()
        frag = (reserved - alloc) / reserved if reserved else 0.0

        # 结构化日志（stderr，父进程 _extract_stderr_message 可解析）
        sys.stderr.write(
            f"[gpu_monitor] free_mib={free/1024/1024:.0f} "
            f"alloc_mib={alloc/1024/1024:.0f} "
            f"reserved_mib={reserved/1024/1024:.0f} "
            f"frag={frag:.2f}\n"
        )
        sys.stderr.flush()

        if free < safety_margin_bytes:
            sys.stderr.write("[gpu_monitor] WARN low_free_mem, empty_cache\n")
            sys.stderr.flush()
            torch.cuda.empty_cache()

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass
```

Launch point: after `initialize` succeeds, `asyncio.create_task(_gpu_monitor(...))`, retain the task reference plus a stop event; on `shutdown`, set stop and await the task.

### 4.2 OOM Fallback (at the DeepSeekOCR2Engine Layer)

```python
async def ocr_batch(self, imgs, output_dir):
    cur_k = len(imgs)
    while cur_k >= 1:
        try:
            return await self._send_ocr_batch(imgs[:cur_k], output_dir) + \
                   (await self.ocr_batch(imgs[cur_k:], output_dir)
                    if cur_k < len(imgs) else [])
        except torch.OutOfMemoryError:
            cur_k = cur_k // 2
            if cur_k < 1:
                raise
            logger.warning("OOM, falling back to batch_size=%d", cur_k)
```

Note: `torch.OutOfMemoryError` is recognized via worker stderr (the worker internally catches `torch.cuda.OutOfMemoryError` and replies with `{"ok": false, "error": "OOM"}`); the parent process inspects the error string to trigger the fallback.

### 4.3 Environment Variables

Append to the worker subprocess env:
```
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

This is the official torch 2.1+ fragmentation-reduction switch; the gains are noticeable for OCR's pattern of repeatedly allocating image tensors of varying sizes.

### 4.4 PaddleOCR Side

ppocr-server's VRAM lives in another process; the worker samples it via pynvml keyed by `CUDA_VISIBLE_DEVICES`. Shares the stderr log format with DeepSeek. Deferred.

## 5. Pipeline Profiler Instrumentation

### 5.1 Module Boundary

Create `backend/docrestore/pipeline/profiler.py`:

```python
class Profiler(Protocol):
    def stage(self, name: str, **attrs: Any) -> AbstractContextManager: ...
    def record_external(self, name: str, duration_s: float, **attrs: Any) -> None: ...
    def export_json(self, path: Path) -> None: ...
    def export_summary_table(self) -> str: ...

class NullProfiler(Profiler):
    """Zero-overhead implementation when disabled."""
    def stage(self, name: str, **attrs: Any) -> AbstractContextManager:
        return _NULL_CTX  # global singleton, nanosecond-cost

class MemoryProfiler(Profiler):
    """Implementation when enabled — events collected into an in-memory list."""
    ...
```

The Pipeline instantiates the appropriate Profiler based on `config.profiling_enable`; the disabled path's `stage()` returns a pre-built no-op context manager (an empty function decorated with `@contextmanager`), at roughly ~50ns per call.

### 5.2 StageEvent Data Structure

```python
@dataclass
class StageEvent:
    name: str                   # "ocr.batch"
    start_ts: float             # time.monotonic()
    duration_s: float
    depth: int                  # 嵌套层级（用于缩进打印）
    attrs: dict[str, Any]       # batch_size=4, image_path="..."
```

### 5.3 Instrumentation Points (Across the Pipeline)

| Stage name | Granularity | Key attrs |
|---|---|---|
| `pipeline.total` | Whole task | task_id, num_images |
| `ocr.phase` | Entire OCR phase | num_images, batch_size |
| `ocr.batch` | Each batch of K images | batch_size, image_paths |
| `ocr.engine.gpu_infer` | Per image, GPU (returned from worker) | image_path, out_tokens |
| `ocr.engine.cpu_postproc` | Per image, CPU (returned from worker) | image_path, n_regions |
| `cleaner.page` | Per-page cleaning | image_path |
| `dedup.merge` | Dedup merge | num_pages |
| `pii.regex` | regex redaction | num_replacements |
| `pii.detect_entities` | LLM entity detection | llm_model, char_count |
| `llm.refine_segment` | Per-segment refinement | segment_idx, char_count, provider |
| `llm.fill_gap` | Per gap | gap_idx, page_filename |
| `llm.final_refine` | Final refinement | char_count |
| `render.write` | Output file write | num_files |

The per-image `profile` returned from the worker is absorbed as an external event via `profiler.record_external("ocr.engine.gpu_infer", duration_s=...)`, then aggregated together with the rest.

### 5.4 Output

When the task ends (`pipeline.total` exits):

1. Write `{output_dir}/profile.json` (full event stream, machine-readable)
2. Print a flattened table to logs (human-friendly):

```
stage                         count    total_s    mean_s   share%
pipeline.total                    1     152.3     152.3    100.0%
  ocr.phase                       1     102.3     102.3     67.2%
    ocr.batch                     9      94.8      10.5       —
    ocr.engine.gpu_infer         36      72.1       2.00     47.3%
    ocr.engine.cpu_postproc      36      18.4       0.51     12.1%
  llm.refine_segment             12      22.5       1.87     14.8%
  pii.detect_entities             1       6.1       6.10      4.0%
  dedup.merge                     1       0.3       0.3       0.2%
  render.write                    1       0.8       0.8       0.5%
```

`share%` is based on `pipeline.total` as 100%.

### 5.5 Toggles

- `PipelineConfig.profiling_enable: bool = False`
- `PipelineConfig.profiling_output_path: str = ""` (empty → `{output_dir}/profile.json`)
- Environment variable `DOCRESTORE_PROFILING=1` overrides the config (handy for debugging)

## 6. New OCRConfig Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `ocr_batch_size` | `int` | `4` | OCR batch size; < 2 falls back to per-image |
| `gpu_monitor_enable` | `bool` | `True` | Enable in-worker GPU monitoring |
| `gpu_monitor_interval_s` | `float` | `1.0` | Sampling interval, in seconds |
| `gpu_memory_safety_margin_mib` | `int` | `1024` | Trigger empty_cache when free drops below this |

## 7. Acceptance Criteria

### 7.1 Functional Acceptance

- [ ] The `ocr_batch_size=1` path behaves identically to the original per-image implementation (all existing OCR tests pass)
- [ ] With `ocr_batch_size=4`, 36 images process correctly and results are equivalent (diff `result.mmd`)
- [ ] When a single image fails, the others still return successfully (the `return_exceptions` path)
- [ ] OOM simulation: deliberately lower `gpu_memory_utilization` to trigger OOM and observe automatic fallback to K/2
- [ ] With `profiling_enable=False`, bench throughput is within 1% of the no-profiler baseline
- [ ] With `profiling_enable=True`, `profile.json` is produced and the table is reasonable

### 7.2 Performance Acceptance (RTX 4070, 36 images × 2 runs)

| Metric | Current baseline | Target |
|---|---:|---:|
| DeepSeek throughput (img/s) | 0.30 | ≥ 0.45 |
| DeepSeek GPU util mean (%) | 52 | ≥ 75 |
| DeepSeek GPU mem peak (MiB) | 9867 | ≤ 11500 |

If a target is missed, roll back or re-evaluate K.

## 8. Rollback Strategy

Each layer has its own toggle and any combination can be disabled:

| Failure | Rollback |
|---|---|
| Batch mode misbehaves | Set `ocr_batch_size=1` to revert to per-image |
| GPU Monitor causes interference | Set `gpu_monitor_enable=False`; the worker won't spawn the background task |
| Profiler bug | Set `profiling_enable=False`; the Pipeline uses NullProfiler |
| Frequent OOM | Lower `ocr_batch_size` or `gpu_memory_utilization` |

## 9. Risks

- **Actual batching behavior of vLLM continuous batching**: AsyncLLMEngine's batching across multiple concurrent generates is decided by the scheduler; the maximum K is jointly bounded by `max_num_seqs` and `gpu_memory_utilization`. Start with a small K (4), observe, then tune.
- **How real fragmentation is in practice**: OCR image tensor sizes are relatively fixed (base_size=1024 plus a small number of crops), so fragmentation should be manageable. If the monitor sees `frag_ratio > 0.3` persistently, evaluate adding periodic `empty_cache`.
- **Profiler event volume**: A single task produces a few hundred events; the JSON file is < 100KB, negligible.
- **PaddleOCR side not done**: This round only lands on DeepSeek. If users switch to PaddleOCR as the primary engine, the single-process serial HTTP remains the bottleneck; will be addressed in a later round.

## 10. Implementation Plan

Proceed in the order of the 9 tasks already listed in the TaskList:

1. Design doc (this file)
2. Profiler infrastructure + PipelineConfig fields
3. OCRConfig field additions
4. DeepSeek worker ocr_batch + pipelining
5. DeepSeek worker GPU Monitor
6. DeepSeekOCR2Engine.ocr_batch layer
7. Pipeline main-loop changes + end-to-end instrumentation
8. Paddle worker concurrent HTTP (deferred)
9. Bench validation + doc updates

Each step has its own commit; task 8 can ship as an independent PR.

