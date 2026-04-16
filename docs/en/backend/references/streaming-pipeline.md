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

> **Note (2026-04-14)**: At the time this document was written, the Pipeline still used
> `llm_override: dict`-style per-request overrides. The Pipeline has since completed
> a Config objectification refactor -- the API layer now synthesizes complete
> `LLMConfig / OCRConfig / PIIConfig` objects in one step and passes them directly
> to downstream components; the `pipeline` internals no longer perform dict merging.
> When reading the pseudocode in this design, interpret `llm_override` and similar
> parameters as `llm: LLMConfig | None` (and `ocr` / `pii` respectively). The design
> rationale remains unchanged; only the parameter types have been upgraded from dicts
> to full Config snapshots.

## 1. Background & Goals

### 1.1 Problem

The current `process_many()` is strictly sequential:

```
OCR all photos (5min) → merge → refine all segments (3min) → split → post-processing
Total time = 5 + 3 = 8min
```

OCR (GPU-intensive) and LLM refinement (network I/O-intensive) use non-overlapping resources, but are scheduled sequentially, causing GPU and network to alternate between being idle.

### 1.2 Goal

Transform OCR and LLM refinement into a **streaming parallel** architecture: as OCR produces results, downstream consumes them. Once enough text accumulates to form a segment, it is immediately sent to the LLM, allowing both stages to overlap:

```
Theoretical speedup ≈ max(OCR_time, LLM_time) ≈ 5min (saving 3min)
```

### 1.3 Engineering Assessment

**Just right**:
- Reuses all existing components (`merge_two_pages`, `refine_one_segment`, `_maybe_fill_gaps`, `Renderer`)
- Standard asyncio Queue + create_task, no new dependencies required
- No-boundary scenarios degrade to single-document, behavior identical to the sequential version
- Not doing: LLM inter-segment concurrency (high complexity, low benefit), progress model overhaul (single channel is sufficient), cross-task queuing (separate topic for AGE-16)

## 2. Design Decisions

| Decision | Conclusion | Rationale |
|----------|-----------|-----------|
| LLM inter-segment concurrency | **Sequential** | DOC_BOUNDARY detection requires ordered processing; out-of-order would cause incorrect document attribution |
| Finalization parallelism | **Continue consuming** | While doc N is being finalized, OCR and the next doc's LLM do not stop, maximizing throughput |
| PII strategy | **Regex first + delayed entity detection** | Accumulate first 5 pages then obtain lexicon; reuse for subsequent pages |
| Progress model | **Single channel unchanged** | OCR/refine report alternately; no frontend changes needed |
| Single-document compatibility | **Write to subdirectory first, move back at the end** | Total document count is unknown during finalization; adjust after all are complete |

## 3. Architecture Overview

### 3.1 Component Relationships

```
┌──────────────┐    Queue[PageOCR|None]    ┌──────────────────────┐
│ OCR Producer │ ────────────────────────▶ │   Stream Processor    │
│  (gpu_lock)  │                           │                       │
└──────────────┘                           │ ┌───────────────────┐ │
    Per-image OCR+cleaning                  │ │IncrementalMerger  │ │
    Does not wait for LLM                   │ │ (incremental      │ │
                                           │ │  merge+tracking)  │ │
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │  Accumulated >=       │
                                           │    segment_size       │
                                           │          ↓            │
                                           │ ┌───────────────────┐ │
                                           │ │StreamSegExtractor │ │
                                           │ │ (extract segment)  │ │
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │ ┌───────────────────┐ │
                                           │ │LLM Refine (serial)│ │
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │   DOC_BOUNDARY?       │
                                           │     ├─ Yes → launch ──│──▶ asyncio.Task
                                           │     │   finalize()    │    (reassemble →
                                           │     │   reset state   │     gap fill →
                                           │     └─ No → continue  │     final refine →
                                           └──────────────────────┘     render)
```

### 3.2 Parallel Timeline

```
Time ──────────────────────────────────────────────────▶
OCR:  [p1][p2][p3] [p4][p5][p6] [p7][p8][p9][p10]
             ↓           ↓                 ↓
LLM:     [seg1]    [seg2+BOUND]      [seg3]  [seg4]
                        ↓                       ↓
Final:            [Doc1: gap→refine→render]  [Doc2: gap→refine→render]
```

- OCR produces PageOCR objects one by one and places them into an asyncio.Queue
- Stream Processor consumes pages, merges incrementally, and sends to LLM once a segment is ready
- LLM returns a result containing DOC_BOUNDARY → background `asyncio.create_task` for finalization
- The stream processor continues consuming subsequent pages during finalization

### 3.3 GPU Lock Contention

The gap fill step in finalization requires `reocr_page` (GPU), which competes with the OCR Producer for `gpu_lock`. Both use `async with gpu_lock` for safe serialization without deadlock. The OCR Producer suspends via await while waiting for the lock, without blocking the event loop.

## 4. Detailed Component Design

### 4.1 IncrementalMerger

**File**: `backend/docrestore/processing/dedup.py` (new class in the same file)

**Responsibility**: Incremental per-page merge, maintaining markdown with page markers, and providing page attribution queries.

```python
class IncrementalMerger:
    """Incremental merger: merges page by page, reusing PageDeduplicator.merge_two_pages()."""

    def __init__(self, config: DedupConfig) -> None:
        """Initialize."""
        self._dedup = PageDeduplicator(config)
        self._raw_text: str = ""                      # Plain text without page markers
        self._page_infos: list[tuple[str, int]] = []   # [(filename, char_offset_in_raw)]
        self._page_images: dict[str, list[Region]] = {} # filename → regions
        self._md_cache: str | None = None              # get_markdown() cache

    def add_page(self, page: PageOCR) -> None:
        """Merge a new page into the accumulated text.

        Implementation:
        1. Rewrite image references: ![](images/N.jpg) → ![]({stem}_OCR/images/N.jpg)
           (reuses PageDeduplicator._rewrite_image_refs logic)
        2. If this is the first page:
           - _raw_text = page_text
           - _page_infos = [(filename, 0)]
        3. Otherwise:
           - result = _dedup.merge_two_pages(_raw_text, page_text)
           - offset = PageDeduplicator._find_page_start(_raw_text, result)
           - _raw_text = result.text
           - _page_infos.append((filename, offset))
        4. _page_images[filename] = page.regions
        5. _md_cache = None (invalidate cache)
        """

    def get_markdown(self) -> str:
        """Return the current complete markdown with page markers.

        Implementation (same as the final stage of merge_all_pages):
        1. If _md_cache exists, return it directly
        2. lines = _raw_text.splitlines(keepends=True)
        3. Iterate _page_infos in reverse order:
           - marker = '<!-- page: {filename} -->\\n'
           - Convert char_offset to line number
           - lines.insert(line_idx, marker)
        4. _md_cache = ''.join(lines).rstrip('\\n')
        5. Return _md_cache
        """

    def get_text_after(self, char_offset: int) -> str:
        """Return get_markdown()[char_offset:]."""

    def get_page_names_up_to(self, page_name: str) -> list[str]:
        """Return a list of all page filenames from the beginning up to and including page_name.

        Purpose: Determine which pages belong to the current document based on DOC_BOUNDARY after_page.
        Returns an empty list if page_name is not found.
        """

    def get_page_names_after(self, page_name: str) -> list[str]:
        """Return a list of all page filenames after page_name (exclusive).

        Purpose: Determine which pages belong to the next document.
        Returns all page names if page_name is not found.
        """

    def get_images_for_pages(self, page_names: set[str]) -> list[Region]:
        """Return all Regions for the specified set of pages."""

    @property
    def total_length(self) -> int:
        """Total character count of the current markdown."""

    @property
    def page_count(self) -> int:
        """Number of pages merged so far."""

    @property
    def all_page_names(self) -> list[str]:
        """List of all merged page filenames (in merge order)."""
```

**Key constraints**:
- `_raw_text` does not contain page markers, avoiding marker interference with `SequenceMatcher`'s overlap detection
- `get_markdown()` is lazily computed + cached; `add_page()` invalidates the cache
- Image reference rewriting must be consistent with `_rewrite_image_refs` in `merge_all_pages`

**Consistency guarantee**: For the same input, `IncrementalMerger`'s `get_markdown()` after sequential `add_page` calls must produce results identical to `PageDeduplicator.merge_all_pages(pages).markdown`. This is a core invariant and must have test coverage.

### 4.2 StreamSegmentExtractor

**File**: `backend/docrestore/processing/segmenter.py` (new class in the same file)

**Responsibility**: Incrementally extract segments from growing text, with backward overlap support.

```python
class StreamSegmentExtractor:
    """Streaming segment extractor: extracts segments on demand from growing text."""

    def __init__(self, max_chars: int = 8000, overlap_lines: int = 5) -> None:
        """Initialize."""
        self._max_chars = max_chars
        self._overlap_lines = overlap_lines
        self._prev_tail_lines: list[str] = []  # Tail lines from previous segment (backward overlap source)

    def try_extract(
        self, full_text: str, offset: int,
    ) -> tuple[str, int] | None:
        """Try to extract a segment from full_text[offset:].

        Condition: extraction only occurs when len(full_text[offset:]) >= max_chars.

        Cut point search range: [offset + max_chars*0.8, offset + max_chars*1.2]
        Cut point priority (highest to lowest):
          1. Heading line (^#{1,6}\\s+)
          2. Page marker line (<!-- page:)
          3. Blank line
          4. Any newline character

        If no cut point is found beyond 1.2x: force cut at offset + max_chars.

        Return value:
          - None: text is not long enough, waiting for more pages
          - (segment_text, new_offset):
            - segment_text includes backward overlap (from _prev_tail_lines)
            - new_offset points to the end position of this segment (excluding overlap), used as offset for the next call

        Side effect: updates _prev_tail_lines with the tail lines of this segment.
        """

    def extract_remaining(
        self, full_text: str, offset: int,
    ) -> tuple[str, int]:
        """Force extract full_text[offset:] as the final segment.

        Does not require sufficient length. Always returns a valid result (may be empty string).
        Includes backward overlap. Updates _prev_tail_lines.
        """

    def reset(self) -> None:
        """Reset state. Called when a new document begins (clears overlap history)."""
        self._prev_tail_lines = []
```

**Backward overlap mechanism**:
- Non-first segments: segment_text = `'\n'.join(_prev_tail_lines) + '\n' + actual_segment`
- First segment: no overlap, returns actual_segment directly
- Forward overlap is not supported (future text is unknown in streaming mode); the quality impact is negligible (the existing Pipeline's `RefineContext.overlap_before/after` is already empty string)

### 4.3 DocumentState

**File**: `backend/docrestore/models.py` (new dataclass)

```python
@dataclass
class DocumentState:
    """Accumulated state for a single document during streaming processing.

    Maintained by _stream_process. When a DOC_BOUNDARY is detected,
    the current DocumentState is passed to _finalize_document, then a new one is created.
    """
    doc_index: int                                          # Document index (0-based)
    title: str = ""                                         # Title
    refined_segments: list[RefinedResult] = field(default_factory=list)
    page_names: list[str] = field(default_factory=list)     # Pages belonging to this document
    images: list[Region] = field(default_factory=list)      # Images belonging to this document
    gaps: list[Gap] = field(default_factory=list)           # Gaps belonging to this document
```

## 5. Detailed Pipeline Refactoring Design

**File**: `backend/docrestore/pipeline/pipeline.py`

### 5.1 process_many() Entry Point Refactoring

Remove the original sequential logic and replace with OCR producer + stream processor startup.

```python
async def process_many(self, image_dir, output_dir, on_progress, llm_override, gpu_lock):
    # 1. Scan images, create output directory (unchanged)
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    images = await asyncio.to_thread(scan_images, image_dir)
    if not images:
        raise FileNotFoundError(f"No image files found: {image_dir}")

    # 2. Create page queue (unbounded buffer, OCR is not blocked)
    page_queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()

    # 3. Start OCR producer (background coroutine)
    ocr_task = asyncio.create_task(
        self._ocr_producer(images, output_dir, gpu_lock, page_queue, _report)
    )

    # 4. Streaming processing main loop
    try:
        results = await self._stream_process(
            page_queue, len(images), output_dir,
            llm_override, gpu_lock, _report,
        )
    finally:
        await ocr_task  # Ensure OCR coroutine completes (await even on exception)

    # 5. Single-document compatibility: if only 1 doc, move subdirectory contents to root
    if len(results) == 1 and results[0].doc_dir:
        results[0] = await self._move_to_root(results[0], output_dir)

    return results if results else [self._empty_result(output_dir)]
```

### 5.2 _ocr_producer()

Extracted from the existing `_ocr_and_clean`, modified to put results into a queue.

```python
async def _ocr_producer(self, images, output_dir, gpu_lock, queue, report_fn):
    """OCR producer: per-image OCR + cleaning, placed into queue. Puts None sentinel when OCR is complete."""
    cleaner = OCRCleaner()
    for i, img in enumerate(images):
        # OCR (protected by gpu_lock)
        if gpu_lock is not None:
            async with gpu_lock:
                page = await self._ocr_engine.ocr(img, output_dir)
        else:
            page = await self._ocr_engine.ocr(img, output_dir)

        # Cleaning
        await cleaner.clean(page)
        await self._save_debug(output_dir, f"{page.image_path.stem}_cleaned.md", page.cleaned_text)

        # Regex PII (per-page, lightweight, does not wait for LLM)
        if self._config.pii.enable:
            redactor = PIIRedactor(self._config.pii)
            page.cleaned_text, _ = redactor.redact_regex_only(page.cleaned_text)

        await queue.put(page)
        report_fn("ocr", i + 1, len(images), f"OCR {i+1}/{len(images)}")

    await queue.put(None)  # Sentinel: all OCR complete
```

**Note**: `PIIRedactor.redact_regex_only()` is a new method -- it only performs structural regex matching (phone/email/ID card/bank card), without entity detection. The existing `redact_snippet` requires a lexicon parameter, which has not been obtained at this point.

### 5.3 _stream_process() (Core Streaming Processor)

```python
async def _stream_process(self, page_queue, total_images, output_dir,
                           llm_override, gpu_lock, report_fn):
    """Consume the OCR page queue: incremental merge + segmented refinement + document splitting.

    Returns list[PipelineResult] (sorted by doc_index).
    """
    llm_cfg = self._resolve_llm_config(llm_override)
    merger = IncrementalMerger(self._config.dedup)
    extractor = StreamSegmentExtractor(
        max_chars=llm_cfg.max_chars_per_segment,
        overlap_lines=llm_cfg.segment_overlap_lines,
    )
    refiner = self._get_refiner(llm_override)

    segmented_offset = 0          # Markdown offset of already-extracted segments
    segment_index = 0             # Global segment index
    current_doc = DocumentState(doc_index=0)
    finalize_tasks: list[asyncio.Task[PipelineResult]] = []
    assigned_pages: set[str] = set()  # Pages assigned to previous documents
    entity_lexicon: EntityLexicon | None = None
    pii_entity_done = False

    # === Main loop: consume OCR pages ===
    while True:
        page = await page_queue.get()
        if page is None:
            break

        merger.add_page(page)

        # Delayed PII entity detection (once after first N pages)
        if (self._config.pii.enable
            and self._config.pii.redact_person_name
            and not pii_entity_done
            and merger.page_count >= _PII_DETECT_THRESHOLD):
            entity_lexicon = await self._delayed_pii_detect(merger, llm_override)
            pii_entity_done = True

        # Try to extract segments and refine
        segmented_offset, segment_index, current_doc = await self._try_extract_and_refine(
            merger, extractor, refiner, segmented_offset, segment_index,
            current_doc, finalize_tasks, assigned_pages,
            output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
        )

    # === Process remaining text ===
    md = merger.get_markdown()
    if segmented_offset < len(md):
        remaining, new_offset = extractor.extract_remaining(md, segmented_offset)
        if remaining.strip():
            result = await self._refine_one_segment(refiner, remaining, segment_index, 0)
            segment_index += 1
            # Handle potential boundary (reuse same logic)
            current_doc = self._handle_refined_result(
                result, current_doc, merger, extractor,
                finalize_tasks, assigned_pages,
                output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
            )

    # === Finalize the last document ===
    remaining_pages = [n for n in merger.all_page_names if n not in assigned_pages]
    current_doc.page_names = remaining_pages
    current_doc.images = merger.get_images_for_pages(set(remaining_pages))
    if not current_doc.title:
        assembled = "\n".join(r.markdown for r in current_doc.refined_segments)
        current_doc.title = extract_first_heading(assembled)

    last_result = await self._finalize_document(
        current_doc, output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
    )

    # === Collect all results ===
    bg_results: list[PipelineResult] = []
    if finalize_tasks:
        bg_results = list(await asyncio.gather(*finalize_tasks))

    all_results = bg_results + [last_result]
    # Sort by doc_index (PipelineResult needs to carry doc_index)
    all_results.sort(key=lambda r: r._doc_index)
    return all_results
```

### 5.4 _try_extract_and_refine() (Extract + Refine Loop)

Inner loop extracted from `_stream_process` to reduce complexity.

```python
async def _try_extract_and_refine(
    self, merger, extractor, refiner, segmented_offset, segment_index,
    current_doc, finalize_tasks, assigned_pages,
    output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
):
    """After merger has new text, try to extract a segment and refine it. May trigger document finalization.

    Returns updated (segmented_offset, segment_index, current_doc).
    """
    md = merger.get_markdown()
    while True:
        seg = extractor.try_extract(md, segmented_offset)
        if seg is None:
            break
        seg_text, new_offset = seg

        result = await self._refine_one_segment(refiner, seg_text, segment_index, 0)
        report_fn("refine", segment_index + 1, 0, f"Refining segment {segment_index + 1}")

        current_doc = self._handle_refined_result(
            result, current_doc, merger, extractor,
            finalize_tasks, assigned_pages,
            output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
        )

        segmented_offset = new_offset
        segment_index += 1

    return segmented_offset, segment_index, current_doc
```

### 5.5 _handle_refined_result() (Process Refined Result + Boundary Detection)

```python
def _handle_refined_result(
    self, result, current_doc, merger, extractor,
    finalize_tasks, assigned_pages,
    output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
) -> DocumentState:
    """Process the refined result of a single segment. Detects DOC_BOUNDARY and may trigger finalization.

    Returns the current or a new DocumentState.
    """
    cleaned_md, boundaries = parse_doc_boundaries(result.markdown)
    cleaned_result = RefinedResult(markdown=cleaned_md, gaps=result.gaps, truncated=result.truncated)

    if not boundaries:
        current_doc.refined_segments.append(cleaned_result)
        current_doc.gaps.extend(result.gaps)
        return current_doc

    # DOC_BOUNDARY found
    boundary = boundaries[0]
    before, after = self._split_refined_at_boundary(cleaned_md, boundary)

    # Complete the current document
    if before.strip():
        current_doc.refined_segments.append(RefinedResult(markdown=before, gaps=result.gaps))
    current_doc.page_names = merger.get_page_names_up_to(boundary.after_page)
    current_doc.images = merger.get_images_for_pages(set(current_doc.page_names))
    assigned_pages.update(current_doc.page_names)
    if not current_doc.title:
        assembled = "\n".join(r.markdown for r in current_doc.refined_segments)
        current_doc.title = extract_first_heading(assembled)

    # Background finalization
    task = asyncio.create_task(
        self._finalize_document(current_doc, output_dir, llm_override, gpu_lock, report_fn, entity_lexicon)
    )
    finalize_tasks.append(task)
    report_fn("finalize", current_doc.doc_index + 1, 0, f"Finalizing document {current_doc.doc_index + 1}")

    # Create new document
    new_doc = DocumentState(doc_index=current_doc.doc_index + 1, title=boundary.new_title)
    extractor.reset()  # Clear overlap history
    if after.strip():
        new_doc.refined_segments.append(RefinedResult(markdown=after))
    return new_doc
```

### 5.6 _split_refined_at_boundary()

```python
@staticmethod
def _split_refined_at_boundary(
    cleaned_md: str,
    boundary: DocBoundary,
) -> tuple[str, str]:
    """Split an already-refined segment in two at the boundary.

    Strategy: Find the page marker corresponding to boundary.after_page,
    then find the next page marker position immediately following it, and split there.

    Returns (before_text, after_text).
    If the corresponding page marker is not found, returns (cleaned_md, "").
    """
    markers = list(_PAGE_MARKER_RE.finditer(cleaned_md))
    # Find the marker corresponding to after_page
    after_idx = None
    for i, m in enumerate(markers):
        if m.group(1).strip() == boundary.after_page:
            after_idx = i
    if after_idx is None:
        return cleaned_md, ""

    # Find the next page marker
    if after_idx + 1 < len(markers):
        split_pos = markers[after_idx + 1].start()
        return cleaned_md[:split_pos], cleaned_md[split_pos:]

    # after_page is the last page → everything belongs to the current document
    return cleaned_md, ""
```

### 5.7 _finalize_document()

```python
async def _finalize_document(
    self, doc_state: DocumentState, output_dir: Path,
    llm_override, gpu_lock, report_fn, entity_lexicon,
) -> PipelineResult:
    """Finalize a single document: reassemble → gap fill → final refine → render.

    Can execute in a background asyncio.Task (concurrent with OCR/LLM).
    gap fill's reocr_page uses gpu_lock to safely compete with the OCR Producer.
    """
    # Reassemble
    reassembled_md = "\n".join(r.markdown for r in doc_state.refined_segments)
    sub_doc = MergedDocument(markdown=reassembled_md, images=doc_state.images)

    # Output directory (always write to subdirectory; single-document compatibility handled by process_many at the end)
    dirname = sanitize_dirname(doc_state.title) or f"document_{doc_state.doc_index + 1}"
    sub_output = output_dir / dirname
    await asyncio.to_thread(sub_output.mkdir, parents=True, exist_ok=True)

    # Gap fill + final refine (reuse existing methods)
    pages_for_gap = ...  # Construct sub_pages from doc_state.page_names
    sub_doc = await self._maybe_fill_gaps(sub_doc, doc_state.gaps, pages_for_gap, ...)
    sub_doc, truncated = await self._do_final_refine(sub_doc, sub_output, ...)

    # Render
    renderer = Renderer(self._config.output)
    doc_path = await renderer.render(sub_doc, sub_output)
    final_md = await asyncio.to_thread(doc_path.read_text, encoding="utf-8")

    doc_dir = sub_output.name
    return PipelineResult(
        output_path=doc_path, markdown=final_md,
        images=sub_doc.images, gaps=doc_state.gaps,
        doc_title=doc_state.title, doc_dir=doc_dir,
        warnings=self._collect_warnings(doc_state.refined_segments, doc_state.gaps, truncated),
        _doc_index=doc_state.doc_index,  # For sorting, not exposed to API
    )
```

### 5.8 _move_to_root() (Single-Document Compatibility)

```python
async def _move_to_root(self, result: PipelineResult, output_dir: Path) -> PipelineResult:
    """Move a single document from its subdirectory to the root directory (backward-compatible output structure).

    Moves document.md and images/ to output_dir, removes the empty subdirectory.
    Updates PipelineResult's output_path and doc_dir.
    """
```

## 6. Delayed PII Entity Detection

```python
_PII_DETECT_THRESHOLD = 5  # Perform entity detection after accumulating 5 pages

async def _delayed_pii_detect(self, merger, llm_override) -> EntityLexicon | None:
    """Perform a single LLM entity detection to obtain the lexicon after the first N pages have accumulated.

    Success: Returns EntityLexicon; subsequent gap fill re-OCR text can reuse it.
    Failure: Returns None; relies solely on regex PII protection.
    Does not block cloud (unlike sequential mode, LLM refinement is already in progress in streaming mode).
    """
```

**New method**: `PIIRedactor.redact_regex_only(text: str) -> tuple[str, list[RedactionRecord]]`
- Only performs structural regex replacement (phone/email/ID card/bank card)
- Does not require EntityLexicon
- Called per-page in `_ocr_producer`

## 7. PipelineResult Temporary Sorting Field

`PipelineResult` gains an internal field for post-finalization sorting:

```python
@dataclass
class PipelineResult:
    # ... existing fields
    _doc_index: int = 0  # For internal sorting, not serialized to API
```

Alternatively, use `dataclasses.field(repr=False, compare=False)` to hide it.
The API schema does not expose this field.

## 8. Progress Reporting

Maintains the single-channel `TaskProgress`; stages appear alternately:

| Stage | current | total | Timing |
|-------|---------|-------|--------|
| `ocr` | i | N (known) | After each image OCR completes |
| `refine` | seg_idx | 0 (unknown) | After each segment refinement completes |
| `finalize` | doc_idx | 0 (unknown) | When document finalization starts |
| `gap_fill` | gi | len(gaps) | During gap fill (reuses existing) |
| `final_refine` | 0 | 1 | Full-document refinement (reuses existing) |
| `render` | 1 | 1 | Render complete (reuses existing) |

The frontend only displays the latest stage; no changes needed.

## 9. Removed/Replaced Code

| Old Method | Disposition |
|-----------|-------------|
| `_ocr_and_clean()` | Replaced by `_ocr_producer()` |
| `_refine_segments()` | Replaced by `_stream_process` inner loop |
| `_reassemble()` | Replaced by inline `"\n".join()` in `_finalize_document` |
| `_split_by_doc_boundaries()` | **Retained** (existing tests depend on it), but no longer called by process_many |

`_split_by_doc_boundaries` is retained because 6 unit tests depend on it, and it can serve as a non-streaming fallback.

## 10. Files to Modify

| File | Operation | Description |
|------|-----------|-------------|
| `backend/docrestore/processing/dedup.py` | Add class | `IncrementalMerger` |
| `backend/docrestore/processing/segmenter.py` | Add class | `StreamSegmentExtractor` |
| `backend/docrestore/models.py` | Add | `DocumentState`; add `_doc_index` to `PipelineResult` |
| `backend/docrestore/pipeline/pipeline.py` | **Refactor** | `process_many` → streaming; add 5+ methods |
| `backend/docrestore/privacy/redactor.py` | Add method | `redact_regex_only()` |
| `tests/processing/test_incremental_merger.py` | Add | Consistency tests |
| `tests/llm/test_stream_segmenter.py` | Add | Cut point/overlap/boundary tests |
| `tests/pipeline/test_streaming_pipeline.py` | Add | Streaming integration tests |
| `docs/modules/pipeline.md` | Update | Streaming architecture description |
| `docs/progress.md` | Update | -- |

## 11. Implementation Order

| Step | Content | Test Requirements |
|------|---------|-------------------|
| 1 | `IncrementalMerger` | get_markdown() after sequential add_page must be identical to merge_all_pages result |
| 2 | `StreamSegmentExtractor` | Correct cut point priority; correct overlap; returns None when < max |
| 3 | `DocumentState` + `PipelineResult._doc_index` | Tested with pipeline tests |
| 4 | `PIIRedactor.redact_regex_only()` | Unit tests |
| 5 | Pipeline refactoring (_ocr_producer + _stream_process + _finalize_document) | Integration tests |
| 6 | Single-document _move_to_root compatibility | Test single/multi-document output directory structure |
| 7 | All existing 236 tests pass | Regression verification |
| 8 | Documentation updates | -- |

## 12. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Incremental merge results differ from batch merge | Strictly reuse `merge_two_pages` + consistency comparison tests |
| DOC_BOUNDARY crosses segment boundary (LLM only sees half the context) | Prefer page markers as cut points to reduce truncation probability; overlap covers transition points |
| Finalization and OCR compete for gpu_lock | Both use `async with gpu_lock` for safe serialization |
| Single-document compatibility (directory structure) | Final `_move_to_root` adjustment |
| Background finalization exception | `asyncio.gather` collects exceptions without affecting other documents |
| OCR Producer exception | Must ensure sentinel is still placed in queue (use try/finally) |
