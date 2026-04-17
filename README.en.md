# DocRestore

Restore a set of document screen-capture photos into Markdown documents in the original format.

**Processing Pipeline**: Photos -> OCR -> Cleaning -> Deduplication & Merging -> PII Redaction (optional) -> LLM Refinement -> Gap Filling -> Markdown Output

## Requirements

- Linux (Ubuntu 20.04+ recommended)
- Python 3.11+, Node.js 18+
- NVIDIA GPU (for running OCR engines; DeepSeek-OCR-2 requires >= 16GB VRAM)
- LLM API Key (litellm-compatible: OpenAI / GLM / Claude / Gemini, etc.)

## Installation

```bash
git clone <repo-url> && cd docrestore

# 1. Backend environment (required, no GPU dependencies)
bash scripts/setup_backend.sh

# 2. OCR engine environment (install at least one)
bash scripts/setup_paddle_ocr.sh      # PaddleOCR (recommended)
bash scripts/setup_deepseek_ocr.sh    # DeepSeek-OCR-2 (fallback)

# 3. Frontend
cd frontend && npm install && cd ..
```

Four conda environments, each with a dedicated role (see [docs/en/deployment.md](docs/en/deployment.md) for details):

| Environment | Purpose | GPU |
|-------------|---------|-----|
| `docrestore` | Backend service (FastAPI/litellm) | No |
| `ppocr_vlm` | PaddleOCR genai_server | Yes |
| `ppocr_client` | PaddleOCR worker | No |
| `deepseek_ocr` | DeepSeek-OCR-2 worker | Yes |

## Getting Started

```bash
# Start everything (backend + frontend)
bash scripts/start.sh all
```

Visit the frontend at http://localhost:5173 (backend API at http://0.0.0.0:8000/api/v1).

OCR engines are managed on demand by `EngineManager`: after the user selects an engine and submits a task in the frontend, the backend automatically starts the corresponding worker (including ppocr-server). When switching engines, the old engine's GPU resources are released automatically.

> If `http_proxy` is set on the system, accessing localhost requires `export no_proxy="localhost,127.0.0.1"` first.

## Configuration

Create a `.env` file in the project root to configure the LLM API Key:

```bash
GEMINI_API_KEY=sk-xxx
# Or OPENAI_API_KEY / GLM_API_KEY
# You can also point to a proxy: OPENAI_API_BASE=https://your-proxy/v1
```

Runtime configuration is controlled via `PipelineConfig` (`backend/docrestore/pipeline/config.py`, pydantic BaseModel):

- `OCRConfig` -- Engine selection, GPU ID, image preprocessing, sidebar filtering
- `DedupConfig` -- Line-level fuzzy matching threshold, overlap context lines
- `LLMConfig` -- Provider (cloud/local), model, API endpoint, segment size, truncation detection, global concurrency cap (`max_concurrent_requests`)
- `OutputConfig` / `PIIConfig` -- Output format, PII redaction

See [docs/en/backend/data-models.md](docs/en/backend/data-models.md) for field descriptions.

## Usage

### Web Frontend

After starting, visit http://localhost:5173:
- Upload images or select a server path to create a task
- WebSocket real-time progress (OCR / cleaning / refinement / output)
- Markdown preview (multi-document sub-document switching) + manual editing + zip download
- Task history: pagination, status filtering, cancel / retry / delete

### Command Line (End-to-End)

```bash
conda activate docrestore && source .env
python scripts/run_e2e.py \
  --input test_images \
  --output output/test \
  --ocr-model paddle-ocr/ppocr-v4
```

### REST API

See [docs/en/backend/api.md](docs/en/backend/api.md) for the full API contract. Examples:

```bash
# Create a task
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"image_dir": "/path/to/images", "output_dir": "/path/to/output"}'

# Download zip results
curl -O http://localhost:8000/api/v1/tasks/{task_id}/download
```

## Output Structure

```
output_dir/
├── {document_title}/           # One subdirectory per document group (single doc degenerates to one group)
│   ├── document.md             # Restored Markdown
│   └── images/                 # Cropped illustrations
└── {stem}_OCR/                 # Per-photo OCR intermediate results (raw text + grounding-cropped images)
```

Content gaps that cannot be automatically filled are marked with GAP markers in the Markdown, along with the source photo filename.

## Development & Testing

```bash
conda activate docrestore

# Backend checks
ruff check backend/
mypy --strict backend/docrestore/
pytest

# Frontend checks and tests
cd frontend && npm run lint && npm test
```

## Project Structure

```
docrestore/
├── backend/docrestore/   # Backend (api / ocr / processing / llm / privacy / pipeline / persistence / output)
├── frontend/             # Frontend (React 19 + Vite + TypeScript strict)
├── scripts/              # Installation / startup / end-to-end scripts
├── tests/                # Backend tests
└── docs/                 # Design documentation (architecture / deployment / backend / frontend / progress)
```

## Documentation

- [System Architecture](docs/en/architecture.md)
- [Deployment Guide](docs/en/deployment.md)
- [Backend Documentation](docs/en/backend/README.md)
- [Frontend Documentation](docs/en/frontend/README.md)
- [Development Progress](docs/en/progress.md)

## License

Apache License 2.0
