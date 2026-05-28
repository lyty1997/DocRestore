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

# Streaming Pipeline Design

> **Status**: Historical design reference. The streaming Pipeline has landed; the current source of truth is `docs/en/backend/pipeline.md` and the code under `backend/docrestore/pipeline/`. This document is retained to record the design reversals, the trade-offs behind disabling DOC_BOUNDARY aggregation, and the rationale for skip-marked tests. Names and signatures may drift from the active implementation.

> **Note (2026-04-14)**: When this document was written, the Pipeline still used
> request-level overrides in the `llm_override: dict` style. The Pipeline has
> since been refactored to use Config objects — the API layer synthesizes the
> complete `LLMConfig / OCRConfig / PIIConfig` once and passes them directly
> downstream, and `pipeline` no longer performs dict merging internally. When
> reading the pseudocode in this design, treat parameters such as `llm_override`
> as `llm: LLMConfig | None` (and `ocr` / `pii`). The design intent is unchanged;
> only the parameter type is upgraded from dict to a complete Config snapshot.

> **Design pivot (2026-04-19)**: This implementation makes three key
> simplifications and reinforcements on top of the original design:
>
> 1. **Drop LLM document aggregation**. In the photo restoration scenario, one
>    subdirectory equals one document, so `process_many` no longer detects
>    `DOC_BOUNDARY` / splits multi-document content / runs parallel finalization.
>    The `parse_doc_boundaries` / `_split_by_doc_boundaries` code and tests are
>    retained for the next-generation code-restoration scenario. `DocumentState`
>    is removed, and `process_many` returns a single `PipelineResult` (the
>    upper-layer `process_tree` aggregates them into a list).
> 2. **Remove LPT scheduling**. The actual `gpu_lock` acquisition order across
>    multiple subdirectories is contaminated by races in upfront async IO
>    (mkdir / scan_images / engine_manager.ensure), so the LPT index ordering
>    does not match the real serialization order and the expected gains cannot
>    be realized. We switch to `process_tree` warmup cold start: sort by page
>    count descending, **run the longest subdirectory serially first** until the
>    `RateController` samples are ready, then `asyncio.gather` the remaining
>    subdirectories in parallel.
> 3. **`RateController` adaptive segment length**. We discard the fixed
>    `max_chars_per_segment` constant and estimate `T_ocr / overhead / k` in
>    real time at runtime using EMA + linear regression, with a closed-form
>    `L*` matching OCR/LLM throughput. Cold start follows a dynamic sampling
>    sequence (1500 → 3000 → 6000); once samples ≥ 3 we switch to the adaptive
>    mode. Different machines / different LLM providers self-personalize.
>
> When reading this document, Section 2 (Decisions), Section 4.1
> IncrementalMerger (page-attribution query methods removed), Section 4.3
> DocumentState (Removed), and Section 5 Pipeline refactoring (single-document
> streaming + RateController) have all been updated to reflect the changes
> above.

## 1. Background & Goals

### 1.1 Problem

The current `process_many()` is strictly serial:

```
OCR all photos (5min) → merge → segmented refine of all (3min) → split → post-process
Total wall time = 5 + 3 = 8min
```

OCR (GPU-bound) and LLM refinement (network-IO-bound) do not share resources,
yet they are scheduled serially, leaving the GPU and network alternately idle.

### 1.2 Goals

Convert OCR and LLM refinement to **streaming parallelism**: as OCR produces
output, downstream consumes it. Once enough text has accumulated for a segment,
it is immediately sent to the LLM, and the two stages overlap:

```
Theoretical speedup ≈ max(OCR time, LLM time) ≈ 5min (saves 3min)
```

### 1.3 Engineering Assessment

**Just right**:
- Reuses every existing component (`merge_two_pages`, `refine_one_segment`, `_maybe_fill_gaps`, `Renderer`)
- Standard asyncio Queue + create_task, no new dependencies
- Degrades to single-document behavior when there are no boundaries, identical to the serial version
- Out of scope: inter-segment LLM concurrency (high complexity, low gain), progress-model overhaul (single channel suffices), cross-task queueing (AGE-16 is a separate topic)

## 2. Design Decisions

| Decision | Conclusion | Rationale |
|------|------|------|
| Inter-segment LLM concurrency | **Serial** | Inter-segment order matches page order; simple and robust, the gain/complexity ratio is unfavorable |
| Document aggregation | **Dropped** (code retained) | Photo restoration scenario: one directory = one document; avoids cross-document contamination caused by LLM boundary false positives. Re-enable when needed in the code-restoration version |
| Multi-subdirectory scheduling | **warmup cold start + gather** | Under `asyncio.gather`, the actual `gpu_lock` acquisition order is contaminated by upfront async IO races, making LPT ordering unstable; the longest subdirectory runs alone serially as warmup, and once `RateController` is ready the remaining subdirectories run concurrently |
| Segment-length parameter | **`RateController` runtime adaptive** | Performance varies widely across machines / LLM providers, so a hard-coded constant has uncontrollable bias; EMA + linear regression estimates `T_ocr / overhead / k`, and the closed-form `L*` matches throughput |
| Cold-start segment length | **Dynamic sequence** (1500 → 3000 → 6000) | Each segment is genuinely refined so nothing is wasted; switch to adaptive once samples ≥ 3 |
| PII strategy | **Regex first + delayed entity detection** | Acquire the lexicon after the first 5 pages, then reuse it |
| Progress model | **Single channel unchanged** | OCR/refine alternate reports, no frontend changes required |

## 3. Architecture Overview

### 3.1 Component Relationships

```
┌──────────────┐    Queue[PageOCR|None]    ┌──────────────────────┐       ┌─────────────┐
│ OCR Producer │ ────────────────────────▶ │   Stream Processor    │ ◀────▶│ RateController│
│  (gpu_lock)  │                           │                       │       │ EMA + regress│
└──────────────┘                           │ ┌───────────────────┐ │       │ outputs L*  │
    OCR + clean per page                   │ │IncrementalMerger  │ │       └─────────────┘
    no waiting for LLM                     │ │  (incremental)    │ │         ▲    ▲
    record_ocr metric                      │ └────────┬──────────┘ │         │    │
                                           │          ↓            │ record_ocr  record_llm
                                           │  on new text          │         │    │
                                           │          ↓            │         │    │
                                           │ ┌───────────────────┐ │         │    │
                                           │ │StreamSegExtractor │ │ ◀─ L* ──┘    │
                                           │ │ (segment by L*)    │ │              │
                                           │ └────────┬──────────┘ │              │
                                           │          ↓            │              │
                                           │ ┌───────────────────┐ │              │
                                           │ │ LLM Refine (serial)│─────metric────┘
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │  collect RefinedResult│
                                           │  (after all segments) │
                                           └──────────────────────┘
                                                      ↓
                                      reassemble → gap fill →
                                      final refine → render
                                                      ↓
                                              PipelineResult (single doc)
```

### 3.2 Parallel Timeline

```
Time ──────────────────────────────────────────────────▶
OCR:  [p1][p2][p3][p4][p5] [p6][p7][p8][p9][p10] [p11..pN]
             ↓                     ↓                    ↓
LLM:       [seg1]               [seg2]               [seg3] [seg4..]
Seg len L: (cold 1500)         (cold 3000)         (L*=?)
                                                              ↓ (OCR/LLM all done)
                                                  reassemble → gap fill →
                                                  final refine → render
                                                              ↓
                                                        PipelineResult
```

- OCR produces `PageOCR` page by page into an `asyncio.Queue`; on completion of each page, `controller.record_ocr(duration)`
- The Stream Processor consumes a page → incremental merge → asks the controller for `L*` → the extractor segments by L*
- After each LLM refinement completes, `controller.record_llm(chars, duration)` updates the regression
- Once all segments are done and the OCR sentinel arrives → reassemble → gap fill → final refine → render
- **No `DOC_BOUNDARY` detection, no parallel finalization**: single directory = single document, finalize only after all segments are collected

### 3.3 GPU Lock Contention

The `reocr_page` call inside gap fill competes with the OCR Producer for
`gpu_lock`. However, gap fill only runs after every segment has been collected
(by which time the OCR Producer has finished and the sentinel has been posted),
so during gap fill there is no contention on `gpu_lock`. In the multi-
subdirectory parallel scenario, OCR Producers / gap fills across subdirectories
are mutually serialized by `gpu_lock`, which is safe and deadlock-free.

## 4. Detailed Component Design

### 4.1 IncrementalMerger

**File**: `backend/docrestore/processing/dedup.py` (new class, same file)

**Responsibility**: Incremental merge page by page, maintain markdown carrying
page markers, and provide page attribution queries.

```python
class IncrementalMerger:
    """Incremental merger: merge page by page, reusing PageDeduplicator.merge_two_pages()."""

    def __init__(self, config: DedupConfig) -> None:
        """Initialize."""
        self._dedup = PageDeduplicator(config)
        self._raw_text: str = ""                      # plain text without page markers
        self._page_infos: list[tuple[str, int]] = []   # [(filename, char_offset_in_raw)]
        self._page_images: dict[str, list[Region]] = {} # filename → regions
        self._md_cache: str | None = None              # get_markdown() cache

    def add_page(self, page: PageOCR) -> None:
        """Merge a new page into the accumulated text.

        Implementation:
        1. Rewrite image references: ![](images/N.jpg) → ![]({stem}_OCR/images/N.jpg)
           (reuses the PageDeduplicator._rewrite_image_refs logic)
        2. If this is the first page:
           - _raw_text = page_text
           - _page_infos = [(filename, 0)]
        3. Otherwise:
           - result = _dedup.merge_two_pages(_raw_text, page_text)
           - offset = PageDeduplicator._find_page_start(_raw_text, result)
           - _raw_text = result.text
           - _page_infos.append((filename, offset))
        4. _page_images[filename] = page.regions
        5. _md_cache = None (clear cache)
        """

    def get_markdown(self) -> str:
        """Return the current full markdown with page markers.

        Implementation (identical to the final stage of merge_all_pages):
        1. If _md_cache exists, return it directly
        2. lines = _raw_text.splitlines(keepends=True)
        3. Iterate _page_infos in reverse:
           - marker = '<!-- page: {filename} -->\\n'
           - convert char_offset to line index
           - lines.insert(line_idx, marker)
        4. _md_cache = ''.join(lines).rstrip('\\n')
        5. return _md_cache
        """

    def get_text_after(self, char_offset: int) -> str:
        """Return get_markdown()[char_offset:]."""

    def get_all_images(self) -> list[Region]:
        """Return the aggregated Region list for all merged pages (for finalization)."""

    @property
    def total_length(self) -> int:
        """Total character count of the current markdown."""

    @property
    def page_count(self) -> int:
        """Number of pages already merged."""

    @property
    def all_page_names(self) -> list[str]:
        """Filenames of all merged pages (in merge order)."""
```

> 2026-04-19 update: in the single-document scenario the per-page-name
> attribution queries are no longer needed; the three methods
> `get_page_names_up_to` / `get_page_names_after` / `get_images_for_pages` have
> been removed from the design. `get_all_images()` is retained for the
> finalization reassemble step.

**Key constraints**:
- `_raw_text` does not contain page markers, preventing markers from disturbing the overlap detection of `SequenceMatcher`
- `get_markdown()` is computed lazily and cached; `add_page()` invalidates the cache
- Image-reference rewriting must be consistent with `_rewrite_image_refs` inside `merge_all_pages`

**Consistency guarantee**: For the same input, calling `add_page` page by page
on `IncrementalMerger` and then `get_markdown()` must produce a result fully
identical to `PageDeduplicator.merge_all_pages(pages).markdown`. This is the
core invariant and must be covered by tests.

### 4.2 StreamSegmentExtractor

**File**: `backend/docrestore/processing/segmenter.py` (new class, same file)

**Responsibility**: Incrementally extract segments from growing text, with
support for backward overlap.

```python
class StreamSegmentExtractor:
    """Streaming segment extractor: extract segments on demand from growing text."""

    def __init__(self, max_chars: int = 8000, overlap_lines: int = 5) -> None:
        """Initialize."""
        self._max_chars = max_chars
        self._overlap_lines = overlap_lines
        self._prev_tail_lines: list[str] = []  # tail lines of the previous segment (backward overlap source)

    def try_extract(
        self, full_text: str, offset: int,
    ) -> tuple[str, int] | None:
        """Try to extract one segment from full_text[offset:].

        Condition: only extract when len(full_text[offset:]) >= max_chars.

        Cut-point search range: [offset + max_chars*0.8, offset + max_chars*1.2]
        Cut-point priority (high to low):
          1. heading line (^#{1,6}\\s+)
          2. page marker line (<!-- page:)
          3. blank line
          4. any newline character

        If no cut point is found within 1.2x: force a cut at offset + max_chars.

        Returns:
          - None: not enough text, wait for more pages
          - (segment_text, new_offset):
            - segment_text contains backward overlap (from _prev_tail_lines)
            - new_offset points to the end of this segment (excluding overlap), used as offset on the next call

        Side effect: update _prev_tail_lines with the tail lines of this segment.
        """

    def extract_remaining(
        self, full_text: str, offset: int,
    ) -> tuple[str, int]:
        """Force-extract full_text[offset:] as the final segment.

        Does not require sufficient length. Always returns a valid result
        (possibly an empty string). Includes backward overlap. Updates
        _prev_tail_lines.
        """

    def reset(self) -> None:
        """Reset state. Called at the start of a new document (clears overlap history)."""
        self._prev_tail_lines = []
```

**Backward overlap mechanism**:
- Non-first segment: segment_text = `'\n'.join(_prev_tail_lines) + '\n' + actual_segment`
- First segment: no overlap, return actual_segment directly
- Forward overlap is unsupported (future text is unknown in streaming mode); the quality impact is negligible (the existing Pipeline's `RefineContext.overlap_before/after` are already empty strings)

### 4.3 DocumentState (Removed)

> 2026-04-19 update: After the single-document simplification, `DocumentState`
> is no longer needed. `_stream_process` simply maintains `list[RefinedResult]`
> + `list[Gap]` as local variables. Reintroduce when document aggregation is
> restored for the code-restoration scenario.

## 5. Detailed Pipeline Refactoring

**File**: `backend/docrestore/pipeline/pipeline.py`

### 5.1 process_many() Simplified to Single-Document Streaming

Remove the original serial logic and switch to launching an OCR producer +
stream processor. **Returns a single `PipelineResult`** (no longer a list);
the upper-layer `process_tree` aggregates multiple subdirectories into a list.

```python
async def process_many(
    self,
    image_dir: Path,
    output_dir: Path,
    on_progress: Callable[[TaskProgress], None] | None = None,
    llm: LLMConfig | None = None,
    gpu_lock: asyncio.Lock | None = None,
    pii: PIIConfig | None = None,
    ocr: OCRConfig | None = None,
    controller: RateController | None = None,
) -> PipelineResult:
    """OCR Producer + Stream Processor single-document streaming.

    When `controller` is non-None, the shared instance is reused
    (`process_tree` reuses it across subdirectories); otherwise this
    process_many creates a temporary instance internally.
    """
    async with self._task_profiler(output_dir) as (profiler, _):
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        images = await asyncio.to_thread(scan_images, image_dir)
        if not images:
            raise FileNotFoundError(f"未找到图片文件: {image_dir}")

        if controller is None:
            controller = RateController(self._config.llm)

        page_queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()
        pages_ref: list[PageOCR] = []  # used for gap fill finalization

        ocr_task = asyncio.create_task(
            self._ocr_producer(
                images, output_dir, gpu_lock, page_queue,
                pages_ref, controller, _report, ocr,
            ),
            name=f"ocr-producer-{image_dir.name}",
        )
        try:
            return await self._stream_process(
                page_queue, pages_ref, image_dir, output_dir,
                llm, gpu_lock, pii, controller, _report,
            )
        finally:
            await ocr_task
```

### 5.2 _ocr_producer()

OCR each page → clean → optional regex-only PII → enqueue. Records the per-page
OCR duration metric. The exception path must also emit the sentinel to avoid
the consumer blocking forever.

```python
async def _ocr_producer(
    self,
    images: list[Path],
    output_dir: Path,
    gpu_lock: asyncio.Lock | None,
    queue: asyncio.Queue[PageOCR | None],
    pages_ref: list[PageOCR],
    controller: RateController,
    report_fn: ReportFn,
    ocr: OCRConfig | None = None,
) -> None:
    """OCR producer: per-page OCR + clean → metric + enqueue + append to pages_ref."""
    engine = await self._resolve_engine(ocr, report_fn)
    cleaner = OCRCleaner()
    pii_cfg = self._config.pii
    redactor = PIIRedactor(pii_cfg) if pii_cfg.enable else None

    try:
        for i, img in enumerate(images):
            t0 = time.perf_counter()
            if gpu_lock is not None:
                async with gpu_lock:
                    page = await engine.ocr(img, output_dir)
            else:
                page = await engine.ocr(img, output_dir)
            await cleaner.clean(page)

            if redactor is not None:
                page.cleaned_text, _ = redactor.redact_regex_only(
                    page.cleaned_text,
                )

            await self._save_debug(
                output_dir,
                f"{page.image_path.stem}_cleaned.md",
                page.cleaned_text,
            )

            controller.record_ocr(time.perf_counter() - t0)
            pages_ref.append(page)
            await queue.put(page)
            controller.set_queue_depth(queue.qsize())
            report_fn(
                "ocr", i + 1, len(images),
                f"OCR {i + 1}/{len(images)}",
            )
    finally:
        await queue.put(None)  # any exception path must enqueue the sentinel
```

**New method**: `PIIRedactor.redact_regex_only(text) -> tuple[str, list[RedactionRecord]]`
performs only structured regex (phone / email / national ID / bank card +
custom sensitive words) and does not rely on the LLM lexicon.

### 5.3 _stream_process() (Single-Document Simplified)

```python
async def _stream_process(
    self,
    page_queue: asyncio.Queue[PageOCR | None],
    pages_ref: list[PageOCR],
    image_dir: Path,
    output_dir: Path,
    llm: LLMConfig | None,
    gpu_lock: asyncio.Lock | None,
    pii: PIIConfig | None,
    controller: RateController,
    report_fn: ReportFn,
) -> PipelineResult:
    """Consume the OCR queue: incremental merge → segment by L* → LLM refine → finalize once collected."""
    merger = IncrementalMerger(self._config.dedup)
    extractor = StreamSegmentExtractor(
        overlap_lines=self._config.llm.segment_overlap_lines,
    )
    refiner = self._get_refiner(llm)

    segmented_offset = 0
    segment_index = 0
    refined_segments: list[RefinedResult] = []
    all_gaps: list[Gap] = []
    entity_lexicon: EntityLexicon | None = None
    pii_entity_done = False
    pii_cfg = pii or self._config.pii

    while True:
        page = await page_queue.get()
        if page is None:
            break
        merger.add_page(page)

        if (pii_cfg.enable
                and not pii_entity_done
                and merger.page_count >= _PII_DETECT_THRESHOLD):
            entity_lexicon = await self._delayed_pii_detect(merger, llm)
            pii_entity_done = True

        segmented_offset, segment_index = await self._try_extract_and_refine(
            merger, extractor, refiner, controller,
            segmented_offset, segment_index,
            refined_segments, all_gaps, report_fn,
        )

    # Final segment: text remaining after OCR has fully finished
    md = merger.get_markdown()
    if segmented_offset < len(md):
        remaining, _ = extractor.extract_remaining(md, segmented_offset)
        if remaining.strip():
            t0 = time.perf_counter()
            result = await self._refine_one_segment(
                refiner, remaining, segment_index, 0,
            )
            controller.record_llm(
                len(remaining), time.perf_counter() - t0,
            )
            refined_segments.append(result)
            all_gaps.extend(result.gaps)
            segment_index += 1
            report_fn(
                "refine", segment_index, 0, f"精修段 {segment_index}",
            )

    return await self._finalize_single_doc(
        merger, pages_ref, refined_segments, all_gaps,
        output_dir, llm, gpu_lock, report_fn, entity_lexicon,
    )
```

### 5.4 _try_extract_and_refine() (Refine Per Dynamic L*)

```python
async def _try_extract_and_refine(
    self,
    merger: IncrementalMerger,
    extractor: StreamSegmentExtractor,
    refiner: LLMRefiner | None,
    controller: RateController,
    segmented_offset: int,
    segment_index: int,
    refined_segments: list[RefinedResult],
    all_gaps: list[Gap],
    report_fn: ReportFn,
) -> tuple[int, int]:
    """After merger has new text, try to segment + refine according to controller.target L*.

    Returns the updated (segmented_offset, segment_index). No boundary
    detection, no finalization dispatch; once all segments are collected,
    _stream_process invokes _finalize_single_doc.
    """
    md = merger.get_markdown()
    while True:
        target = controller.target_segment_chars()
        seg = extractor.try_extract(md, segmented_offset, target)
        if seg is None:
            break
        seg_text, new_offset = seg

        t0 = time.perf_counter()
        result = await self._refine_one_segment(
            refiner, seg_text, segment_index, 0,
        )
        controller.record_llm(
            len(seg_text), time.perf_counter() - t0,
        )
        refined_segments.append(result)
        all_gaps.extend(result.gaps)
        segmented_offset = new_offset
        segment_index += 1
        report_fn(
            "refine", segment_index, 0, f"精修段 {segment_index}",
        )

    return segmented_offset, segment_index
```

### 5.5 _finalize_single_doc() (Finalize Once All Segments Are Collected)

```python
async def _finalize_single_doc(
    self,
    merger: IncrementalMerger,
    pages_ref: list[PageOCR],
    refined_segments: list[RefinedResult],
    all_gaps: list[Gap],
    output_dir: Path,
    llm: LLMConfig | None,
    gpu_lock: asyncio.Lock | None,
    report_fn: ReportFn,
    entity_lexicon: EntityLexicon | None,
) -> PipelineResult:
    """Single document: reassemble → gap fill → final refine → render."""
    doc = self._reassemble(refined_segments, MergedDocument(
        markdown="", images=merger.get_all_images(), gaps=[],
    ))
    await self._save_debug(output_dir, "reassembled.md", doc.markdown)

    doc = await self._maybe_fill_gaps(
        doc, all_gaps, pages_ref, output_dir, llm, gpu_lock,
        report_fn, entity_lexicon,
    )
    doc, truncated = await self._do_final_refine(
        doc, output_dir, llm, report_fn,
    )

    renderer = Renderer(self._config.output)
    doc_path = await renderer.render(doc, output_dir)
    final_md = await asyncio.to_thread(
        doc_path.read_text, encoding="utf-8",
    )

    return PipelineResult(
        output_path=doc_path,
        markdown=final_md,
        images=doc.images,
        gaps=all_gaps,
        doc_title=extract_first_heading(doc.markdown),
        doc_dir="",  # single document lands directly in output_dir root, no subdirectory created
        warnings=self._collect_warnings(
            refined_segments, all_gaps, truncated,
        ),
    )
```

### 5.6 RateController (Adaptive Segment Length)

**File**: `backend/docrestore/pipeline/rate_controller.py` (new)

```python
class RateController:
    """Estimate T_ocr / overhead / k at runtime and emit the target segment length L*.

    Data model:
      R_ocr = chars_per_page / T_ocr         # OCR throughput (chars/s)
      R_llm(L) = L / (overhead + k · L)      # LLM throughput (chars/s)
      Equate the two → L* = R_ocr · overhead / (1 - R_ocr · k)
      R_ocr·k ≥ 1 (LLM cannot keep up however large L gets) → L* = MAX, amortizing overhead

    Interface:
      record_ocr(duration: float) → None
          Recorded on every page OCR completion; EMA updates T_ocr and chars_per_page.
      record_llm(chars: int, duration: float) → None
          Recorded on every segment LLM completion (chars, duration); sliding-window least-squares regression on overhead/k.
      target_segment_chars() → int
          Cold start (samples < 3) → dynamic sequence [1500, 3000, 6000][sample_count]
          Adaptive (samples ≥ 3) → closed-form L*, clamped to [1500, 12000], with ±30% rate-of-change clamp
      set_queue_depth(n: int) → None
          Observation metric (only for post-mortem profile.json, not used as feedback control)
      wait_cold_start() → None
          Awaits until samples ≥ 3 or 60s timeout, used to synchronize process_tree warmup
      cold_start_done: asyncio.Event

    Cold-start fallback (60s timeout):
      Already have 1-2 LLM samples → use duration/chars as the k estimate, overhead=0, enter adaptive mode
      0 samples → keep MIN_CHARS, force cold_start_done set; other subdirectories start as usual
                 and continue sampling internally until the regression becomes usable.

    Threading / concurrency:
      Internally use an asyncio lock to protect the regression sample list; the EMA can be lock-free
      (single writer coroutine / sequential updates).
      With multiple subdirectories running concurrently, record_* is called from multiple coroutines,
      so the sample append / EMA must be lock-protected.

    Observability:
      State snapshot written to profile.json:
        ocr_avg_ms / chars_per_page_avg / llm_overhead_ms / llm_per_char_ms
        samples_llm / cold_start_elapsed_s / final_target_chars
    """
```

### 5.7 process_tree Parallel Branch (Warmup Cold Start)

**File**: `backend/docrestore/pipeline/pipeline.py`

```python
async def process_tree(
    self,
    image_dir: Path,
    output_dir: Path,
    on_progress: Callable[[TaskProgress], None] | None = None,
    llm: LLMConfig | None = None,
    gpu_lock: asyncio.Lock | None = None,
    pii: PIIConfig | None = None,
    ocr: OCRConfig | None = None,
) -> list[PipelineResult]:
    leaf_dirs = await asyncio.to_thread(find_image_dirs, image_dir)
    if not leaf_dirs:
        raise FileNotFoundError(f"未找到图片文件: {image_dir}")

    # Single directory: delegate directly, no warmup
    if len(leaf_dirs) == 1 and leaf_dirs[0] == image_dir:
        result = await self.process_many(
            image_dir, output_dir, on_progress,
            llm, gpu_lock, pii, ocr,
        )
        return [result]

    # Multiple subdirectories: warmup the longest one, then concurrent for the rest
    controller = RateController(self._config.llm)
    leaves_sorted = sorted(
        leaf_dirs, key=lambda p: (-_count_images(p), str(p)),
    )
    warmup_leaf, *rest = leaves_sorted

    warmup_task = asyncio.create_task(
        self._process_leaf(
            0, warmup_leaf, image_dir, output_dir,
            on_progress, llm, gpu_lock, pii, ocr,
            total=len(leaves_sorted), controller=controller,
        ),
        name=f"warmup-leaf-{warmup_leaf.name}",
    )

    # Wait for the controller cold start to be ready (samples ≥ 3 or 60s timeout)
    await controller.wait_cold_start()

    # Concurrently launch the remaining subdirectories, reading the controller's current L*
    rest_tasks = [
        asyncio.create_task(
            self._process_leaf(
                i + 1, leaf, image_dir, output_dir,
                on_progress, llm, gpu_lock, pii, ocr,
                total=len(leaves_sorted), controller=controller,
            ),
            name=f"leaf-{leaf.name}",
        )
        for i, leaf in enumerate(rest)
    ]

    results = list(await asyncio.gather(warmup_task, *rest_tasks))
    return results
```

**Key points**:
- Removed `_sort_leaves_lpt`: the new ordering is solely used to "pick the longest directory for warmup" and no longer pretends to be LPT scheduling.
- During warmup, no other subdirectory is started at all (no contention for `gpu_lock`, no sampling interference, regression samples stay clean).
- After other subdirectories start, the `target_segment_chars()` they read is the closed-form `L*` based on the warmup samples.
- Exception semantics: any leaf failing → `asyncio.gather` fail-fast → the entire task FAILED (consistent with the original semantics).
- The shared `controller` lets every subdirectory's `record_ocr/record_llm` metrics stack uniformly, so the steady-state estimate becomes more accurate over time.

## 6. Delayed PII Entity Detection

```python
_PII_DETECT_THRESHOLD = 5  # perform entity detection after accumulating 5 pages

async def _delayed_pii_detect(self, merger, llm_override) -> EntityLexicon | None:
    """Run a single LLM entity detection after the first N pages have accumulated to obtain the lexicon.

    Success: return EntityLexicon, reusable by the re-OCR text in subsequent gap fill.
    Failure: return None, falling back to regex-only PII protection.
    Does not block the cloud (unlike the serial mode, the LLM refinement is already in progress in streaming mode).
    """
```

**New method**: `PIIRedactor.redact_regex_only(text: str) -> tuple[str, list[RedactionRecord]]`
- Performs only structured regex replacements (phone / email / national ID / bank card)
- Does not require an EntityLexicon
- Called per page inside `_ocr_producer`

## 7. PipelineResult Temporary Sorting Field (Removed)

> 2026-04-19 update: After the single-document simplification, `_doc_index` is
> no longer needed. The upper-layer `process_tree` retains the list returned by
> `gather` directly in subdirectory order (sorted by page count descending),
> with no further sorting required.

## 8. Progress Reporting

Keep the single-channel `TaskProgress`; stages alternate:

| stage | current | total | When |
|-------|---------|-------|------|
| `ocr` | i | N (known) | Each OCR page completion |
| `refine` | seg_idx | 0 (unknown) | Each segment refinement completion |
| `gap_fill` | gi | len(gaps) | During gap fill (reuses existing) |
| `final_refine` | 0 | 1 | Whole-document refinement (reuses existing) |
| `render` | 1 | 1 | Render completion (reuses existing) |

- The `finalize` stage is dropped (no more "parallel finalization" semantics).
- `_wrap_progress` in `process_tree` continues to prefix the message with `[i/N {subdir}]`, and the frontend displays per-subtask tracks (consistent with the existing logic).

## 9. Removed/Replaced Code

| Old method | Disposition |
|--------|------|
| `_ocr_and_clean()` | Replaced by `_ocr_producer()` |
| `_refine_segments()` | Replaced by the `_stream_process` main loop + `_try_extract_and_refine` |
| `_sort_leaves_lpt()` | **Removed**. `process_tree` switches to "longest subdirectory as warmup" + plain ordering |
| `_redact_pii()` (global batch version) | Split into (a) per-page `redact_regex_only` inside `_ocr_producer`; (b) `_delayed_pii_detect` asynchronously fetching the lexicon when merger.page_count ≥ 5 |
| `_detect_doc_boundaries()` / `_insert_doc_boundaries()` / `_split_by_doc_boundaries()` / `_handle_refined_result()` / `_split_refined_at_boundary()` | **Code retained** (unit tests still depend on it), but `process_many` no longer invokes it. Re-enable in the next-generation code-restoration scenario |
| `_reassemble()` | Continues to be reused (called by `_finalize_single_doc`) |
| `_finalize_document()` / `_move_to_root()` | Not needed (single document lands directly in the `output_dir` root) |

## 10. Files to Modify

| File | Action | Notes |
|------|------|------|
| `backend/docrestore/pipeline/rate_controller.py` | **New** | `RateController` class (EMA + regression + L\* + cold-start sequence + `wait_cold_start`) |
| `backend/docrestore/processing/dedup.py` | New class | `IncrementalMerger` (with `add_page` / `get_markdown` / `get_all_images` / `page_count` / `all_page_names`) |
| `backend/docrestore/processing/segmenter.py` | New class | `StreamSegmentExtractor` (`try_extract(text, offset, max_chars)` / `extract_remaining` / `reset`) |
| `backend/docrestore/privacy/redactor.py` | New method | `PIIRedactor.redact_regex_only()` |
| `backend/docrestore/pipeline/pipeline.py` | **Refactor** | `process_many` → single-document streaming; `process_tree` parallel branch → warmup cold start; remove `_sort_leaves_lpt` |
| `backend/docrestore/pipeline/config.py` | Update | `LLMConfig` removes the hard default `max_chars_per_segment` (or change it to the cold-start sequence terminal value as a fallback) |
| `backend/docrestore/models.py` | No change | No new field on `PipelineResult` (no `_doc_index`) |
| `tests/pipeline/test_rate_controller.py` | New | EMA / regression / cold-start sequence / timeout fallback |
| `tests/processing/test_incremental_merger.py` | New | After per-page `add_page`, `get_markdown()` is fully identical to `merge_all_pages(pages).markdown` |
| `tests/processing/test_stream_segmenter.py` | New | Cut-point priority, dynamic `max_chars`, backward overlap, `< max_chars` returns None |
| `tests/pipeline/test_process_tree_parallel.py` | Update assertions | Remove the LPT expectation; verify the timing of "warmup runs first, the others wait until cold start before launching" |
| `tests/pipeline/test_pipeline.py` | Update assertions | `process_many` returns a single `PipelineResult` (no longer a list); related assertions follow the change |
| `tests/pipeline/test_boundary_gap_combo.py` / `tests/llm/test_doc_boundary.py` | Mark skip | Unit tests retained; the pipeline-level integration is skipped (unskipped in the code-restoration version) |
| `docs/zh/backend/pipeline.md` / `docs/zh/progress.md` | Update | Streaming architecture + progress log |

## 11. Implementation Order

| Step | Content | Acceptance |
|------|------|------|
| 1 | Revert the previous diagnostic logs (routes.py / pipeline.py) | Done |
| 2 | `RateController` + unit tests | EMA / regression / cold-start sequence / timeout fallback all covered |
| 3 | `IncrementalMerger` + unit tests | For some real subdirectory of parallel_test/, the incremental merge result is fully identical to `merge_all_pages` |
| 4 | `StreamSegmentExtractor` + unit tests | Cut points and overlap correct under dynamic max_chars |
| 5 | `PIIRedactor.redact_regex_only` | Both structured regex and custom sensitive-word replacement work |
| 6 | Refactor `process_many` to streaming (single document) | The original `_refine_segments` / `DOC_BOUNDARY` paths are entirely bypassed; mock tests pass |
| 7 | `process_tree` parallel-branch warmup cold start + remove LPT | Update assertions in `test_process_tree_parallel.py`; the new expectation "remaining subdirectories run concurrently after warmup completes" passes |
| 8 | Full regression on existing tests | All pass except the 2 marked-skip files |
| 9 | E2E comparison against the `parallel_test/基础系统` baseline (289s) | New implementation wall-time ≤ baseline * 0.75; the controller's steady-state L\* is visible in `profile.json` |
| 10 | Record measured numbers in `docs/zh/progress.md` | —— |

## 12. Risks & Mitigations

| Risk | Mitigation |
|------|------|
| Inconsistency between incremental merge and batch merge | Strictly reuse `merge_two_pages` + a consistency comparison test (step 3) |
| RateController estimate oscillates / runs away | The closed-form `L*` is a convergent function of the observations; ±30% rate-of-change clamp; clamp [1500, 12000] as a hard guardrail |
| LLM throughput < OCR (`R_ocr·k ≥ 1`) | Fallback `L* = MAX` (amortizing overhead) + warning; total wall time ≈ total chars / (1/k) is dominated by the LLM |
| The warmup subdirectory itself is too short to gather 3 samples | Shrink the dynamic sequence (when remaining chars are insufficient for the next target, merge into the next segment); with 2 samples, use a simple (duration, chars) estimate to enter adaptive mode |
| LLM fully fails during cold start | 60s timeout fallback (conservative L = 1500), warning records `cold_start_failed` |
| OCR Producer exception | `try/finally` guarantees the sentinel is enqueued; `process_many`'s `finally` blocks `await ocr_task` so the exception propagates out |
| gap fill contends with OCR Producer for gpu_lock | Gap fill happens after OCR has fully ended (single-document flow), so there is no contention; in the multi-subdirectory parallel scenario there is no conflict within the same subdirectory, and across subdirectories `gpu_lock` mutually serializes them |
| Removing DOC_BOUNDARY regression coverage | `parse_doc_boundaries` / `_split_by_doc_boundaries` unit tests are retained; integration-level `test_boundary_gap_combo.py` is marked `@pytest.mark.skip(reason="流式版停用文档聚合，下一版解锁")` |

