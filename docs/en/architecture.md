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

# DocRestore System Architecture

## 1. Project Overview

DocRestore restores consecutively captured document photos into formatted Markdown documents (with illustrations).

Core challenges:
- Adjacent photos overlap, causing OCR output to contain duplicate/repeated content that requires algorithmic deduplication and stitching into continuous body text
- The original document structure must be preserved as much as possible (headings, lists, tables, code blocks, illustration references)
- The OCR model stays resident on GPU to support continuous processing of multiple photos; LLM refinement supports configurable cloud/local providers

## 2. System Architecture

```
┌───────────────────────────────────────────────────────────┐
│                      Web Frontend Layer                    │
│     React SPA (Upload, Progress, Result Preview, History) │
└───────────────────────┬───────────────────────────────────┘
                        │ HTTP + WebSocket (Bearer Token)
┌───────────────────────▼───────────────────────────────────┐
│                      Public API Layer                      │
│     FastAPI REST + WebSocket + Chunked Upload + Token Auth │
│  /tasks  /uploads  /sources  /filesystem  /results  ...    │
└───────────────────────┬───────────────────────────────────┘
                        │
┌───────────────────────▼───────────────────────────────────┐
│                  Pipeline Orchestration Layer               │
│   TaskManager (SQLite Persistence) + Pipeline (Scheduling) │
└─────────┬───────────┬───────────┬───────────┬─────────────┘
          │           │           │           │
┌─────────▼───┐ ┌─────▼─────┐ ┌───▼────────┐ ┌────▼─────┐ ┌────▼─────┐
│  OCR Layer   │ │ Clean/Dedup│ │ PII/Privacy│ │ LLM Layer│ │Output Lyr│
│ OCREngine(*) │ │ Cleaner+   │ │ Redactor(*)│ │Refiner(*)│ │ Renderer │
│EngineManager │ │ Dedup+Merge│ │ (optional) │ │(optional)│ │          │
└──────────────┘ └───────────┘ └────────────┘ └──────────┘ └──────────┘
(* Abstract interface, implementations are swappable)
```

### 2.1 Layer Responsibilities

| Layer | Responsibility | Input | Output |
|-------|---------------|-------|--------|
| Web Frontend | User interaction, progress display, result preview | User actions | HTTP/WS requests |
| API Layer | Receive requests, task management, progress push | HTTP/WS requests | JSON responses |
| Pipeline Layer | Orchestrate processing workflow, schedule stages | Task config + image directory | `PipelineResult` |
| Processing Layer | Independent processing logic (OCR/cleaning/LLM/output) | Previous stage data objects | Current stage data objects |

### 2.2 Engineering Assessment

This four-layer architecture is **just right**:
- Not over-engineered: OCR, deduplication/merging, privacy redaction, LLM refinement, and output rendering differ entirely in their dependencies (GPU/cloud) and failure modes, naturally requiring isolation
- Not under-engineered: mixing OCR/dedup/LLM/redaction together would make backend replacement, debugging, and regression testing extremely difficult
- Abstracting OCR/LLM/privacy interfaces is necessary: the backend is explicitly required to be configurable and must support graceful degradation on failure

## 3. Data Flow

```
Step 1: OCR -> Step 2: Cleaning -> Step 3: Dedup & Merge -> Step 4: PII Redaction (optional)
    -> Step 5: Segmentation -> Step 6: LLM Refinement -> Step 7: Reassembly
    -> Step 8: Multi-doc Boundary Detection (optional) -> [each sub-document enters the following]
    -> Step 9: Gap Filling (optional) -> Step 10: Full-text Refinement (optional) -> Step 11: Output
```

Detailed description:
- Step 1 - OCR: Run OCR on each photo, generating a `{stem}_OCR/` directory per page
- Step 2 - Cleaning: Intra-page deduplication, garbled text/blank line repair
- Step 3 - Dedup & Merge: Rolling merge of adjacent pages, cross-page frequency filtering (`strip_repeated_lines`) to remove sidebar noise, insert `<!-- page: ... -->` boundary markers
- Step 4 - PII Redaction (optional): Structured regex (phone/email/national ID/bank card) + LLM entity detection, producing an `EntityLexicon` for reuse on re-OCR fragments
- Step 5 - Segmentation: Split by headings/blank lines, adjacent segments retain `overlap_lines` lines of context
- Step 6 - LLM Refinement: Per-segment markdown structure repair, Gap marker parsing, model truncation detection (`finish_reason == "length"` or heuristic line-count ratio)
- Step 7 - Reassembly: Concatenate segment results, aggregate gaps and warnings
- Step 8 - Multi-doc Boundary Detection (optional): `LLMRefiner.detect_doc_boundaries()` as an independent LLM call, splitting the merged text into multiple `PipelineResult` instances
- Step 9 - Gap Filling (optional): `OCREngine.reocr_page()` re-OCR + `LLMRefiner.fill_gap()`, with GPU lock and per-gap exception fallback
- Step 10 - Full-text Refinement (optional): Final full-text refinement pass, re-running `parse_gaps()`
- Step 11 - Output: `Renderer` aggregates illustrations with copy/rename, writing to `doc_dir` (root directory for single document / subdirectories for multiple documents)

## 4. Directory Structure

```
docrestore/
├── backend/docrestore/
│   ├── api/              # FastAPI application and routes (REST + WebSocket + file upload)
│   ├── pipeline/         # Pipeline orchestration and scheduling
│   ├── ocr/              # OCR engines (subprocess workers + EngineManager on-demand switching)
│   ├── processing/       # Cleaning and deduplication
│   ├── privacy/          # PII redaction
│   ├── llm/              # LLM refinement (cloud / local)
│   ├── persistence/      # SQLite task persistence
│   ├── output/           # Markdown rendering output
│   ├── utils/            # Utility functions
│   └── models.py         # Data models
├── frontend/             # React 19 + TypeScript + Vite frontend
├── tests/                # Tests
├── docs/                 # Documentation
└── scripts/              # Installation and startup scripts
```

## 5. Key Technical Decisions

### 5.1 OCR Engine Selection and On-Demand Switching
- Primary engine: PaddleOCR (lightweight document parsing)
- Fallback engine: DeepSeek-OCR-2 (high-accuracy grounding OCR, requires large-VRAM GPU)
- **Unified subprocess architecture**: Both engines run as subprocess workers in their respective conda environments, communicating via JSON Lines protocol; the backend does not directly depend on torch/vllm
- **EngineManager**: Switches engines on demand; only one engine occupies the GPU at any given time. After the user selects an engine in the frontend, the backend automatically starts/stops the corresponding worker and ppocr-server
- OCR Router: Unified factory function that creates the corresponding engine based on model identifier

### 5.2 Deduplication Algorithm
- Uses `difflib.SequenceMatcher` for fuzzy line matching
- More robust against minor OCR differences, with moderate computational cost

### 5.3 LLM Refinement Strategy
- Preferentially splits by headings to maintain semantic coherence
- Adjacent segments retain overlap for context (embedded in `Segment.text`, deduplicated by LLM during refinement)
- Supports two providers: cloud (litellm) and local (OpenAI-compatible API: vLLM / ollama / llama.cpp)
- Dual-layer truncation detection: model `finish_reason` + output/input line-count ratio heuristic threshold (`LLMConfig.truncation_*`)

### 5.4 Multi-Document Boundary Detection
- Performed by an independent LLM call `detect_doc_boundaries()` (decoupled from segment refinement)
- Merged text is sent to the LLM, which returns `list[DocBoundary]` (JSON fault-tolerant; parse failure falls back to single document)
- `Pipeline.process_many()` splits sub-documents based on boundaries; each sub-document independently undergoes gap fill / final refine / render
- Output directory: single document writes to `output_dir/`; multiple documents write to `output_dir/{sanitize_dirname(title)}/`; dirname conflicts are resolved by `dedupe_dirnames()` appending suffixes

### 5.5 Concurrency Model
- GPU serialization (`asyncio.Lock` protecting OCR calls + engine switching)
- `EngineManager.switch_lock` prevents concurrent switching; waits for the current OCR operation to release `gpu_lock` before switching engines
- No group-level concurrency (single task monopolizes GPU); task-level concurrency is controlled by TaskManager
- Streaming parallel Pipeline design document: `docs/backend/references/streaming-pipeline.md` (pending implementation)

## 6. Extensibility Design

### 6.1 Swappable Components
- OCR engine: implement the `OCREngine` Protocol
- LLM refinement: implement the `LLMRefiner` Protocol
- PII redaction: implement the `PIIRedactor` interface

### 6.2 Future Extension Directions
- IDE code screenshots -> source files
- PDF input support
- Streaming parallel Pipeline implementation (AGE-16, design completed)
- Frontend multi-document result display (AGE-33)

## 7. Related Documentation

- [Backend Documentation Index](backend/README.md)
- [Frontend Documentation Index](frontend/README.md)
- [Deployment Guide](deployment.md)
- [Development Progress](progress.md)
