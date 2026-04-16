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

# DocRestore Backend Architecture

## 1. Module Structure

```
backend/docrestore/
├── models.py              # Data objects
├── pipeline/
│   ├── config.py          # PipelineConfig (pydantic BaseModel)
│   ├── pipeline.py        # Pipeline orchestration
│   ├── task_manager.py    # Task lifecycle (with SQLite persistence)
│   └── scheduler.py       # Global scheduler (GPU Lock)
├── ocr/
│   ├── base.py            # OCREngine Protocol + WorkerBackedOCREngine
│   ├── deepseek_ocr2.py   # DeepSeek-OCR-2 implementation
│   ├── paddle_ocr.py      # PaddleOCR implementation
│   ├── router.py          # OCR Router (automatic engine selection)
│   ├── engine_manager.py  # EngineManager (on-demand switching + GPU exclusion)
│   ├── preprocessor.py    # Image preprocessing
│   ├── ngram_filter.py    # NoRepeatNGram processor
│   └── column_filter.py   # Sidebar filtering
├── processing/
│   ├── cleaner.py         # OCR output cleaning
│   ├── dedup.py           # Adjacent page deduplication and merging
│   └── segmenter.py       # Document segmenter
├── llm/
│   ├── base.py            # LLMRefiner Protocol + BaseLLMRefiner
│   ├── cloud.py           # CloudLLMRefiner (litellm + PII entity detection)
│   ├── local.py           # LocalLLMRefiner (OpenAI-compatible local service)
│   └── prompts.py         # Prompt templates
├── privacy/
│   ├── patterns.py        # Structured PII regex patterns
│   └── redactor.py        # PIIRedactor + EntityLexicon
├── persistence/
│   └── database.py        # TaskDatabase (SQLite task persistence)
├── output/
│   └── renderer.py        # Markdown rendering output
├── utils/
│   └── paths.py           # Path utilities
└── api/
    ├── app.py             # FastAPI application factory (with auto environment detection)
    ├── routes.py          # REST routes + WebSocket routes
    ├── upload.py          # Chunked upload sessions
    ├── auth.py            # Bearer Token authentication
    └── schemas.py         # API request/response schemas
```

## 2. Module Documentation Index

| Module | Documentation | Core Interfaces |
|--------|--------------|-----------------|
| Data Models & Configuration | [data-models.md](data-models.md) | `PageOCR`, `MergedDocument`, `PipelineResult`, `PipelineConfig` |
| OCR Layer | [ocr.md](ocr.md) | `OCREngine.ocr()`, `OCREngine.ocr_batch()`, `EngineManager` |
| Processing Layer | [processing.md](processing.md) | `OCRCleaner.clean()`, `PageDeduplicator.merge_all_pages()` |
| LLM Refinement Layer | [llm.md](llm.md) | `LLMRefiner.refine()`, `fill_gap()`, `final_refine()` |
| PII Redaction | [privacy.md](privacy.md) | `PIIRedactor.redact_for_cloud()` |
| Pipeline Orchestration | [pipeline.md](pipeline.md) | `Pipeline.process_many()`, `TaskManager` |
| API Layer | [api.md](api.md) | REST + WebSocket + Upload + Authentication |

## 3. Module Dependencies

```
api/app.py
    ├─ api/auth.py        (Bearer Token authentication)
    ├─ api/upload.py      (Chunked upload sessions)
    └─ api/routes.py
        → pipeline/task_manager.py
            → persistence/database.py (SQLite persistence)
            → pipeline/scheduler.py   (GPU Lock)
            → pipeline/pipeline.py
                → ocr/router.py + ocr/engine_manager.py
                    → ocr/deepseek_ocr2.py
                    → ocr/paddle_ocr.py
                → processing/cleaner.py
                → processing/dedup.py
                → llm/cloud.py / llm/local.py
                → privacy/redactor.py
                → output/renderer.py
        → models.py (shared by all modules)
```

Dependency rules:
- `models.py` and `pipeline/config.py` are the common foundation and do not depend on other modules
- Processing-layer modules (ocr/processing/llm/privacy/output) are in principle independent of each other
- Only `pipeline.py` is aware of all processing-layer modules and orchestrates them
- The API layer depends only on the Pipeline layer, not directly on the processing layer

## 4. Data Flow Overview

```
Photo list [img1, img2, ..., imgN]
    |
    v  Process each image (serialized via GPU Lock)
    for each image:
      (1) OCR (ocr) -> PageOCR
      (2) Clean (clean) -> PageOCR (cleaned_text populated)
    |
    v  (3) Dedup & merge (merge_all_pages) -> MergedDocument
    |
    v  (4) PII redaction (optional) -> MergedDocument (redacted)
    |
    v  (5) Segment (segment) -> list[Segment]
    |
    v  (6) LLM refinement (refine x M segments) -> list[RefinedResult]
    |
    v  (7) Reassemble (_reassemble) -> MergedDocument
    |
    v  (8) Gap auto-fill (optional) -> MergedDocument
    |
    v  (9) Full-document refinement (optional) -> MergedDocument
    |
    v  (10) Output (render) -> document.md
    |
    -> PipelineResult
```

## 5. Programming Interface

```python
from pathlib import Path
from docrestore import Pipeline, PipelineConfig
from docrestore.pipeline.config import LLMConfig

config = PipelineConfig(
    llm=LLMConfig(model="anthropic/claude-sonnet-4-20250514"),
)

pipeline = Pipeline(config)
await pipeline.initialize()

results = await pipeline.process_many(
    image_dir=Path("/path/to/photos"),
    output_dir=Path("/path/to/output"),
)

# results: list[PipelineResult] (LLM clustering may split into multiple documents)
# results[0].output_path        -- .md file path
# results[0].markdown           -- markdown content
# results[0].images             -- all illustrations
# results[0].gaps               -- detected content gaps
# results[0].warnings           -- pipeline warnings
# results[0].redaction_records  -- PII redaction statistics

await pipeline.shutdown()
```

## 6. Error Handling Strategies

### 6.1 OCR Failure
- Single page failure: log a warning, allow the pipeline to continue
- Engine crash: retry initialization a limited number of times; if all retries fail, the task fails

### 6.2 LLM Refinement Failure
- Single segment failure: fall back to the original text, log a warning, do not interrupt the pipeline
- Gap fill failure: degrade to "mark only, no fill"

### 6.3 Deduplication Failure
- No overlap found: concatenate directly
- Match score too low: retain more original text, let the LLM handle it

## 7. Related Documentation

- [Data Models](data-models.md)
- [OCR Layer](ocr.md)
- [Processing Layer](processing.md)
- [LLM Layer](llm.md)
- [Pipeline](pipeline.md)
- [API](api.md)
