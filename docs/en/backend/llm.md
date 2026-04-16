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
- **(Cloud-only) PII entity detection**: Provides person/organization name entity dictionaries for the privacy redaction stage.

Two **providers** are supported -- cloud and local:

- Cloud: Calls various cloud models (Claude / GPT / GLM, etc.) via LiteLLM.
- Local: Calls local models via an OpenAI-compatible API (vLLM / ollama / llama.cpp, etc.).

> Design principle: **Strictly no compression, summarization, or rewriting of valid content**; only fix formatting errors, remove obvious duplicates, and insert gap markers / fill gap content.

## 2. File List

| File | Responsibility |
|---|---|
| `llm/base.py` | `LLMRefiner` Protocol + `BaseLLMRefiner` shared implementation (litellm calls, refine/fill_gap/final_refine/detect_doc_boundaries/detect_pii_entities) |
| `llm/cloud.py` | `CloudLLMRefiner(BaseLLMRefiner)` (cloud implementation, overrides `detect_pii_entities` for real entity detection) |
| `llm/local.py` | `LocalLLMRefiner(BaseLLMRefiner)` (local implementation, `detect_pii_entities` inherits the default empty implementation) |
| `llm/prompts.py` | Prompt templates + GAP parsing (`parse_gaps()`, etc.) |

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

    async def detect_doc_boundaries(
        self, merged_markdown: str,
    ) -> list[DocBoundary]: ...

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]: ...
```

**Calling conventions**:
- Input: a single markdown segment (`raw_markdown`) with `RefineContext` (segment index and other context)
- Output: `RefinedResult(markdown, gaps, truncated)`
  - `gaps`: a list of `Gap` objects parsed from the LLM output (the LLM expresses gap locations via comment markers)
  - `truncated`: whether the model output was suspected to be truncated (see Section 6)
- `detect_doc_boundaries()`: Detects multi-document boundaries in the full merged text, returning `list[DocBoundary]`; degrades to `[]` (single document) if JSON parsing fails or a non-array is returned
- `detect_pii_entities()`: Default empty implementation (data stays local in the local scenario); `CloudLLMRefiner` overrides it with real LLM-based entity recognition

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
- Document boundary detection `detect_doc_boundaries()`
- PII entity detection `detect_pii_entities()` (returns empty lists by default; overridden by cloud)
- Output truncation marking (`finish_reason == "length"` -> `truncated=True`)

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

    async def detect_doc_boundaries(
        self, merged_markdown: str,
    ) -> list[DocBoundary]: ...

    async def detect_pii_entities(
        self, text: str,
    ) -> tuple[list[str], list[str]]:
        """默认返回 ([], [])，CloudLLMRefiner 覆盖为真实检测"""
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

`CloudLLMRefiner(BaseLLMRefiner)` overrides `detect_pii_entities` to perform LLM-based entity recognition:

- The prompt is constructed by `build_pii_detect_prompt()`
- The model is expected to return JSON: `{"person_names": [...], "org_names": [...]}`
- JSON parsing failures raise `RuntimeError` (upstream decides whether to block cloud calls)

### 5.3 `LocalLLMRefiner` (llm/local.py)

`LocalLLMRefiner(BaseLLMRefiner)` is the implementation for the local provider:

- Purely inherits the base class's `refine()/fill_gap()/final_refine()/detect_doc_boundaries()`
- `detect_pii_entities()` inherits the base class's empty implementation (data stays local in the local scenario; PII redaction relies solely on regex and custom sensitive words)

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
- PII entity detection:
  - `PII_DETECT_SYSTEM_PROMPT`
  - `build_pii_detect_prompt(text)`
- Multi-document boundary:
  - `DOC_BOUNDARY_SYSTEM_PROMPT`
  - `build_doc_boundary_detect_prompt(merged_markdown)`
  - `parse_doc_boundaries(llm_response) -> list[DocBoundary]` (JSON fault-tolerant)
  - `extract_first_heading(markdown) -> str` (fallback naming for untitled sub-documents)

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

- During the redaction stage, LLM entity detection goes through `BaseLLMRefiner.detect_pii_entities()`: the base class returns `([], [])` by default
- `CloudLLMRefiner` overrides this method with real LLM detection; `LocalLLMRefiner` inherits the default empty implementation -> only regex-based redaction is performed

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
    ├─ (optional) PII redaction: CloudLLMRefiner.detect_pii_entities()
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
