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

# LLM Refinement Layer (llm/)

## 1. Responsibilities

The LLM refinement layer performs "format repair + structure restoration + gap detection" on the merged and deduplicated OCR markdown, and provides two additional capabilities:

- **Automatic gap filling (Gap fill)**: When refinement detects a content jump, it extracts the missing fragment from re-OCR results and inserts it back.
- **Whole-document refinement (Final refine)**: Performs a second pass of cross-segment deduplication and global format cleanup on the reassembled markdown.

> **Cloud LLM PII entity detection removed in S4 (2026-06-15)**: the cloud `detect_pii_entities` method chain (`CloudLLMRefiner.detect_pii_entities` / `BaseLLMRefiner.detect_pii_entities` / the `LLMRefiner` Protocol declaration) and the `build_pii_detect_prompt` / `PII_DETECT_SYSTEM_PROMPT` prompts have all been deleted. Person/organization detection now runs on **local NER** (`privacy/ner.py::SpacyEntityDetector`, surfaced via `PIIGuard.detect_entities` -- names never leave the machine). See [privacy.md](privacy.md).

Two **providers** are supported -- cloud and local:

- Cloud: Calls various cloud models (Claude / GPT / GLM, etc.) via LiteLLM.
- Local: Calls local models via an OpenAI-compatible API (vLLM / ollama / llama.cpp, etc.).

> Design principle: **Strictly no compression, summarization, or rewriting of valid content**; only fix formatting errors, remove obvious duplicates, and insert gap markers / fill gap content.

## 2. File List

| File | Responsibility |
|---|---|
| `llm/base.py` | `LLMRefiner` Protocol + `BaseLLMRefiner` shared implementation (litellm calls, refine/fill_gap/final_refine; cloud `detect_pii_entities` removed in S4, replaced by local NER) |
| `llm/cloud.py` | `CloudLLMRefiner(BaseLLMRefiner)` (cloud implementation; now a thin subclass kept as the `provider="cloud"` selection marker after `detect_pii_entities` was removed in S4) |
| `llm/local.py` | `LocalLLMRefiner(BaseLLMRefiner)` (local implementation, inherits `refine/fill_gap/final_refine`) |
| `llm/prompts.py` | Prompt templates + GAP parsing (`parse_gaps()`, etc.) |
| `llm/code_refine.py` | `CodeLLMRefiner` (code-mode character-level refine / rewrite mode) |
| `llm/code_repair.py` | `DiagnosticCodeRepairer` (diagnostic-driven scoped patches) + `CodeConsistencyAuditor` (re-diagnosis + acceptance gate) |

> The document segmenter `DocumentSegmenter` has been moved to `processing/segmenter.py` (see [Processing Layer](processing.md)). Segmentation does not depend on an LLM; it is pure text processing.

## 3. Public Interface

### 3.1 LLMRefiner Protocol (llm/base.py)

Pipeline calls LLM refinement capabilities through this Protocol. All methods are protocol members (with default implementations in the base class); no `hasattr` capability probing is done at runtime.

```python
class LLMRefiner(Protocol):
    async def refine(
        self, raw_markdown: str, context: RefineContext,
    ) -> RefinedResult: ...

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str: ...

    async def final_refine(self, markdown: str) -> RefinedResult: ...
```

> The cloud `detect_pii_entities` Protocol method was removed in S4 (2026-06-15); person/organization detection now runs on local NER (`privacy/ner.py::SpacyEntityDetector` via `PIIGuard.detect_entities`).

**Calling conventions**:
- Input: a single markdown segment (`raw_markdown`) with `RefineContext` (segment index and other context)
- Output: `RefinedResult(markdown, gaps, truncated)`
  - `gaps`: a list of `Gap` objects parsed from the LLM output (the LLM expresses gap locations via comment markers)
  - `truncated`: whether the model output was suspected to be truncated (see Section 6)

## 4. Dependencies

| Source | Usage |
|---|---|
| `models.py` | `RefinedResult`, `Gap`, `RefineContext`, `Segment` |
| `pipeline/config.py` | `LLMConfig` |

The LLM layer does not depend on the implementation details of OCR/processing/output; it only consumes text and produces text plus structured markers.

## 5. Internal Implementation

### 5.1 `BaseLLMRefiner` (llm/base.py)

`BaseLLMRefiner` is the shared implementation for both cloud and local providers, encapsulating:

- LiteLLM call parameter assembly (model, retries, timeout, base_url/api_key, etc.)
- Per-segment refinement `refine()`
- Gap filling `fill_gap()`
- Whole-document refinement `final_refine()`
- Output truncation marking (`finish_reason == "length"` -> `truncated=True`)

> The cloud `detect_pii_entities()` method was removed in S4 (2026-06-15); entity detection now runs on local NER (see [privacy.md](privacy.md)).

Interface structure:

```python
class BaseLLMRefiner:
    def __init__(self, config: LLMConfig) -> None: ...

    def _build_kwargs(
        self, messages: list[dict[str, str]]
    ) -> dict[str, object]: ...

    async def refine(
        self, raw_markdown: str, context: RefineContext
    ) -> RefinedResult: ...

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str: ...

    async def final_refine(self, markdown: str) -> RefinedResult: ...
```

Key points:
- `refine()`:
  1) `build_refine_prompt()` generates messages
  2) `litellm.acompletion()` call
  3) `parse_gaps()` extracts `Gap` markers from LLM output and cleans the markers themselves
- `fill_gap()`:
  - Uses `build_gap_fill_prompt()` to have the LLM "extract the missing fragment" from re-OCR text
  - If the model returns `GAP_FILL_EMPTY_MARKER = "无法补充"`, returns an empty string indicating the gap could not be filled
- `final_refine()`:
  - Uses `build_final_refine_prompt()` for whole-document deduplication (cross-segment duplicates, headers, watermarks, etc.)

### 5.2 `CloudLLMRefiner` (llm/cloud.py)

`CloudLLMRefiner(BaseLLMRefiner)` is now a thin subclass that serves purely as the `provider="cloud"` selection marker; it inherits the base class's `refine()/fill_gap()/final_refine()` unchanged.

> Previously this class overrode `detect_pii_entities` to perform LLM-based entity recognition (cloud JSON detection via `build_pii_detect_prompt()`). That override -- together with its JSON-parsing helpers -- was removed in S4 (2026-06-15); person/organization detection migrated to local NER so names never leave the machine.

### 5.3 `LocalLLMRefiner` (llm/local.py)

`LocalLLMRefiner(BaseLLMRefiner)` is the implementation for the local provider:

- Purely inherits the base class's `refine()/fill_gap()/final_refine()`

### 5.4 Prompt Templates and GAP Parsing (llm/prompts.py)

`prompts.py` contains all prompt templates and parsing logic:

- Per-segment refinement:
  - `REFINE_SYSTEM_PROMPT`
  - `REFINE_USER_TEMPLATE`
  - `build_refine_prompt(raw_markdown, context)`
- Whole-document refinement:
  - `FINAL_REFINE_SYSTEM_PROMPT`
  - `FINAL_REFINE_USER_TEMPLATE`
  - `build_final_refine_prompt(markdown)`
- Gap filling:
  - `GAP_FILL_SYSTEM_PROMPT`
  - `GAP_FILL_USER_TEMPLATE`
  - `GAP_FILL_EMPTY_MARKER = "无法补充"`
  - `build_gap_fill_prompt(gap, current_page_text, next_page_text?, next_page_name?)`
- Heading extraction:
  - `extract_first_heading(markdown) -> str` (takes the first heading as `PipelineResult.doc_title`)

GAP marker parsing:

- `parse_gaps(refined_markdown) -> (cleaned_markdown, gaps)`
- Target format:
  - `<!-- GAP: after_image=filename, context_before="preceding text", context_after="following text" -->`
- **Fault-tolerance strategy**: Regex does best-effort matching; markers with missing fields or malformed formats are silently ignored -- no errors, no interruption.

> Important: The refinement prompt depends only on page boundary markers `<!-- page: <original_image_filename> -->` and GAP markers; it no longer relies on any "inter-segment transition markers."

### 5.5 Provider Selection and PII Compatibility

Provider selection is done by Pipeline:

- `LLMConfig.provider == "cloud"` -> `CloudLLMRefiner`
- `LLMConfig.provider == "local"` -> `LocalLLMRefiner`

PII compatibility strategy:

- The LLM layer no longer participates in PII entity detection. The cloud `detect_pii_entities()` path was removed in S4 (2026-06-15).
- Person/organization detection now runs on local NER (`privacy/ner.py::SpacyEntityDetector`, surfaced via `PIIGuard.detect_entities`); regex + custom sensitive words still apply on top. Names never leave the machine regardless of cloud/local provider. See [privacy.md](privacy.md).

### 5.6 CodeLLMRefiner (llm/code_refine.py, Code-Mode Character-Level Refine)

Wraps `BaseLLMRefiner._call_llm` to run an independent LLM call on every `SourceFile.merged_text`; **does not reuse** the markdown-refine truncation fallback or GAP parsing. Two modes selected via `LLMConfig.code_refine_mode`:

| Mode | Behavior | Parse keys | Line-count constraint |
|---|---|---|---|
| `refine` (default) | Character-level fixes (whitelist: OCR noise, obvious typos) | `corrected_code` / `corrections` / `unresolved` | Strict `output == input`; violations fall back to the original |
| `rewrite` | Allow reformatting, merging broken lines, supplying compilation-required syntax | `rewritten_code` / `summary` | No line-count constraint (structural fix priority) |

Entry point: `async def refine(source: SourceFile) -> CodeRefineResult`. Large files are auto-chunked via `_should_chunk_refine`, refined per chunk, and concatenated; line-number offsets across corrections / unresolved entries are fixed by `_offset_corrections` / `_offset_unresolved`. Failure / timeout / parse failure all fall back to the original text + a `code.refine.*` flag, never raising and interrupting the rest of the task.

Returns `CodeRefineResult(refined_text, flags, corrections=[CodeCorrection], unresolved=[CodeUnresolved])`; Pipeline writes back into `SourceFile.merged_text` and `flags`.

### 5.7 DiagnosticCodeRepairer (llm/code_repair.py, Diagnostic-Driven Scoped Repair)

Applies to `SourceFile`s the diagnoser reports as `syntax_dirty`. Flow:

1. **build_repair_contexts** (runs in a thread because of rglob / read_text blocking IO) carves multiple small windows around each diagnostic's `failing_lines` ± `window_radius`; windows do not overlap (`_merge_line_windows` keeps a gap).
2. Call the LLM per window to obtain a scoped patch (`ScopedPatch` with `edit_range` + `new_text`).
3. **Line-number remapping**: both `patch` and `edit_range` reference original-text line numbers; later patches shift their coordinates by the cumulative `line_offset` of earlier accepted patches before being applied to `current` (B7 C2 key fix).
4. **Line-count conservation fallback**: truncating patches (`_is_truncating_patch`) are treated as rejected, preventing the LLM from cutting off the tail of a window.
5. **Isolated diagnosis must co-locate sibling files**: when re-diagnosing the repaired copy, repair places same-directory header files alongside, otherwise missing-include errors get classified as `dependency_dirty (score 0)` and spoof the acceptance gate (B7 C13 self-review follow-up).

On failure / no windows / all-rejected, returns the original text plus a `code.repair.no_windows` or `code.repair.reject_*` flag.

### 5.8 CodeConsistencyAuditor (llm/code_repair.py, Post-Repair Acceptance Gate)

Second-pass review after repair. Flow:

1. If repair **changed the line count**, re-diagnose against the rewritten text (`diagnose_source_files([audit_source])`) — reusing the pre-refine diagnostics on original line numbers would misalign authorization windows (B7 C3 key fix).
2. `audit()` takes the `previous_result` and re-diagnosed result, accepts a patch when "the diagnostic score did not degrade", and rejects + reverts to the pre-repair text otherwise.
3. The audit's `flags` / `unresolved` are merged into the final `CodeRefineResult`.

## 6. Truncation Detection

Truncated refinement output can cause:
- Missing document tail
- Unclosed code blocks / tables / lists
- Incomplete GAP markers

The system uses two-level detection and writes the result to `RefinedResult.truncated`:

1) **Model-level signal**: When `litellm` returns `finish_reason == "length"`, it is directly classified as `truncated=True`.

2) **Heuristic signal (Pipeline layer)**: When the model does not explicitly mark truncation, but the output line count drops abnormally relative to the input line count (line-count ratio threshold + minimum input line count), Pipeline marks the segment result as suspected truncation and emits a warning log.

   Heuristic thresholds come from `LLMConfig` and take effect per task:

   | Field | Default | Meaning |
   |---|---|---|
   | `truncation_ratio_threshold` | `0.3` | Flagged as truncated when output lines < `input lines x (1 - ratio)` |
   | `truncation_min_input_lines` | `20` | Heuristic not triggered when input lines <= this value (small samples have high false-positive rates) |

   The heuristic is applied only when the refiner self-reports `truncated=False` (to avoid double classification).

Finally, Pipeline aggregates truncation warnings from all segments and the whole-document refinement, returning them as result warnings to upstream.

## 7. Data Flow (Integration with Pipeline)

A typical call path of the LLM layer within the full processing flow (non-LLM module details omitted):

```
MergedDocument.markdown
    │
    ├─ (optional) PII redaction: local NER PIIGuard.detect_entities()  # cloud detect_pii_entities removed in S4 (2026-06-15)
    │
    ▼
processing.segmenter.DocumentSegmenter.segment()
    │
    ▼
BaseLLMRefiner.refine()  x N segments
    │    └─ parse_gaps() -> gaps
    ▼
Pipeline._reassemble()  # simple join
    │
    ├─ (optional) Gap filling: BaseLLMRefiner.fill_gap()  + OCR.reocr_page()
    │
    └─ (optional) Whole-document refinement: BaseLLMRefiner.final_refine()
         └─ parse_gaps() (final refinement may also produce new gaps)
```
