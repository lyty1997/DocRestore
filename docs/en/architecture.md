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

Doc mode is a **streaming producer/consumer**: OCR produces while the LLM consumes, and one directory is treated as one document.

```
Doc mode (_stream_pipeline):
  (1) OCR producer: per-page OCR -> (2) Cleaning -> (3) optional regex PII -> push into page_queue
                                       || (concurrent)
  (4) Streaming consumer: per-page incremental merge -> (5) segment by L* -> (6) LLM segment refinement
       ((7) once 5 pages accumulate, asynchronously fetch the PII lexicon)
  After all pages collected, finalize: (8) reassemble -> (9) gap filling (optional) -> (10) full-text refinement (optional)
              -> (11) programmatic dedup fallback -> (12) output -> a single PipelineResult

Code mode branch (_code_pipeline):
  (1) OCR text_lines -> (2) IDE layout / line-number column detection -> (3) code-column assembly
    -> (4) cross-page grouping by path/filename into SourceFile -> (5) LLM character-level refinement/repair
    -> (6) lightweight diagnostics -> (7) output files/, files-index.json, and a compatible Markdown
```

Detailed description (doc mode):
- (1) OCR: Run OCR on each photo, generating a `{stem}_OCR/` directory per page; OCR is serialized under `gpu_lock`
- (2) Cleaning: Intra-page deduplication, garbled text/blank line repair
- (3) PII regex (optional): During production, per-page `redact_regex_only` (phone/email/national ID/bank card) redacts first
- (4) Incremental merge: `IncrementalMerger.add_page()` rolling merge/dedup page by page, inserting `<!-- page: ... -->` boundary markers
- (5) Streaming segmentation: `StreamSegmentExtractor` cuts segments from the growing text by the runtime-adaptive segment length L* driven by `RateController`
- (6) LLM Refinement: Per-segment markdown structure repair, Gap marker parsing, model truncation detection (`finish_reason == "length"` or heuristic line-count ratio); on `LLMCache` hit it is skipped; on failure it falls back to the original text
- (7) PII entity detection (optional): After 5 pages accumulate, asynchronously call `detect_pii_entities()` to obtain an `EntityLexicon` for reuse on gap-filling re-OCR fragments
- (8) Reassembly: `_reassemble()` concatenates segment results
- (9) Gap Filling (optional): `OCREngine.reocr_page()` re-OCR + `LLMRefiner.fill_gap()`, with GPU lock and per-gap exception fallback
- (10) Full-text Refinement (optional): Final full-text refinement pass, re-running `parse_gaps()`
- (11) Programmatic dedup fallback: zero-LLM-cost removal of duplicate HTML tables / H2 sections / visual code-block line numbers / residual UI noise
- (12) Output: `Renderer` aggregates illustrations with copy/rename, writing to `output_dir/document.md`
- Code mode output: `render_code_files()` writes `output_dir/files/**`, `files-index.json`, and `document.md`; `files-index.json` is the source of truth for the frontend CodeViewer's file list, source pages, quality flags, and diagnostics

## 4. Directory Structure

```
docrestore/
├── backend/docrestore/
│   ├── api/              # FastAPI application and routes (REST + WebSocket + file upload)
│   ├── pipeline/         # Pipeline orchestration and scheduling
│   ├── ocr/              # OCR engines (subprocess workers + EngineManager on-demand switching)
│   ├── processing/       # Cleaning, deduplication, IDE layout, code assembly and diagnostics
│   ├── privacy/          # PII redaction
│   ├── llm/              # LLM refinement (cloud / local) and code refinement/repair
│   ├── persistence/      # SQLite task persistence
│   ├── output/           # Markdown rendering and code-mode file output
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
- Streaming segmentation by headings/blank lines, with segment length L* adapted at runtime by `RateController`
- Adjacent segments retain backward overlap for context (embedded in the segment text, deduplicated by the LLM during refinement)
- Supports two providers: cloud (litellm) and local (OpenAI-compatible API: vLLM / ollama / llama.cpp)
- Dual-layer truncation detection: model `finish_reason` + output/input line-count ratio heuristic threshold (`LLMConfig.truncation_*`)
- The code refine mode auto-chunks large `SourceFile`s by line/character count, and a single failed chunk only falls back to that chunk; the rewrite mode does not auto-chunk

### 5.4 Concurrency Model
- GPU serialization (`asyncio.Lock` protecting OCR calls + engine switching)
- `EngineManager.switch_lock` prevents concurrent switching; waits for the current OCR operation to release `gpu_lock` before switching engines
- No group-level concurrency (single task monopolizes GPU); task-level concurrency is controlled by TaskManager
- **Streaming parallelism is implemented**: within `process_many` the OCR producer runs concurrently with the LLM consumer; for `process_tree` with multiple subdirectories, the longest directory does a warmup cold start and then the rest run concurrently, sharing a single `RateController`. For the origin of this design reversal see `docs/en/backend/references/streaming-pipeline.md`; the source of truth is the `pipeline/` code and `backend/pipeline.md`

## 6. Extensibility Design

### 6.1 Swappable Components
- OCR engine: implement the `OCREngine` Protocol
- LLM refinement: implement the `LLMRefiner` Protocol
- PII redaction: implement the `PIIRedactor` interface

### 6.2 OCR Contract for Code Mode
- Code mode does not bind to a specific OCR provider; the API and config layers must not force a switch to PaddleOCR.
- Code mode only depends on the abstract artifact `PageOCR.text_lines`: any OCR engine that populates line-level `bbox/text/score` can plug into the IDE layout analysis chain.
- When the active OCR engine does not provide `text_lines`, code mode must fail explicitly with a capability error rather than silently skip pages or fall back to doc mode.

### 6.3 Current Boundaries and Future Extensions
- Code mode already supports IDE code photos -> source files, source-image linkage, lightweight diagnostics, and single-file manual edit/save; further enhancements remain possible: function-level chunking, project-level dependency graphs, and a mature code editor component
- PDF input support
- Frontend multi-document result display has a basic navigation in place; end-to-end visual verification with real fixtures is still pending

## 7. Related Documentation

- [Backend Documentation Index](backend/README.md)
- [Frontend Documentation Index](frontend/README.md)
- [Deployment Guide](deployment.md)
- [Development Progress](progress.md)
