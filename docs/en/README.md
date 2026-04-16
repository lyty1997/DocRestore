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

# DocRestore Document Index

## Project Overview

DocRestore restores consecutively captured document photos into formatted Markdown documents (with illustrations).

Core capabilities:
- OCR recognition (DeepSeek-OCR-2 / PaddleOCR)
- Adjacent page deduplication and merging
- LLM refinement (cloud / local)
- PII redaction (optional)
- Web interface and REST API

## Documentation Structure

```
docs/en/
├── README.md                    # This file (Document Index)
├── architecture.md              # System Architecture
├── deployment.md                # Deployment Guide
├── progress.md                  # Development Progress
├── backend/                     # Backend Documentation
│   ├── README.md                # Backend Architecture Overview
│   ├── data-models.md           # Data Models & Configuration
│   ├── ocr.md                   # OCR Layer
│   ├── processing.md            # Processing Layer
│   ├── llm.md                   # LLM Refinement Layer
│   ├── privacy.md               # PII Redaction
│   ├── pipeline.md              # Pipeline Orchestration
│   ├── api.md                   # REST API
│   └── references/              # Reference Documentation
│       ├── deepseek-ocr2.md     # DeepSeek-OCR-2 Reference
│       └── streaming-pipeline.md # Streaming Pipeline Design (Pending)
└── frontend/                    # Frontend Documentation
    ├── README.md                # Frontend Architecture Overview
    ├── tech-stack.md            # Tech Stack & Engineering Standards
    └── features.md              # Features & Interaction Design
```

## Quick Navigation

### Getting Started
1. [System Architecture](architecture.md) - Understand the overall design
2. [Deployment Guide](deployment.md) - Environment setup and startup
3. [Backend Architecture](backend/README.md) - Backend module structure
4. [Frontend Architecture](frontend/README.md) - Frontend tech stack

### Backend Development
- [Data Models](backend/data-models.md) - Core data structures and configuration
- [OCR Layer](backend/ocr.md) - OCR engine interfaces and implementations
- [Processing Layer](backend/processing.md) - Cleaning and deduplication algorithms
- [LLM Layer](backend/llm.md) - Refinement interfaces and prompts
- [Pipeline](backend/pipeline.md) - Workflow orchestration and scheduling
- [API](backend/api.md) - REST and WebSocket interfaces

### Frontend Development
- [Tech Stack](frontend/tech-stack.md) - TypeScript/React/Vite standards
- [Feature Design](frontend/features.md) - Interaction flows and state management

### Reference
- [DeepSeek-OCR-2 Reference](backend/references/deepseek-ocr2.md)
- [Development Progress](progress.md)

## Documentation Maintenance Rules

- Architecture changes: update the corresponding module documentation first, then modify code
- Interface changes: must synchronously update `data-models.md` and related module documentation
- New features: record in `progress.md`, then update the corresponding module documentation upon completion
- Design decisions: record in the "Design Decisions" section of the corresponding module documentation
