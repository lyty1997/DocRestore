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

# Show full help
bash scripts/start.sh --help
```

Visit the frontend at http://localhost:5173 (backend API at http://0.0.0.0:8000/api/v1).

### Modes

| Mode | Description |
|------|-------------|
| `all` (default) | Start backend + frontend; the frontend is launched after the backend lifespan is ready |
| `backend` | Backend only (uvicorn + `docrestore.api.app`) |
| `frontend` | Frontend only (Vite dev server) |
| `ppocr-server` | PaddleOCR `genai_server` only (vLLM backend, standalone process) |
| `-h` / `--help` | Show help |

### Environment Variables

Export before the command to override defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_HOST` | `0.0.0.0` | Backend bind address |
| `BACKEND_PORT` | `8000` | Backend port |
| `FRONTEND_PORT` | `5173` | Vite dev server port |
| `PPOCR_GPU_ID` | empty | Pin GPU id; if empty, `CUDA_VISIBLE_DEVICES` is left unset and vLLM enumerates all GPUs while `gpu_detect.pick_best_gpu` picks the one with most free VRAM |
| `PPOCR_PORT` | `8119` | PaddleOCR `genai_server` port |
| `PPOCR_MODEL` | `PaddleOCR-VL-1.5-0.9B` | PaddleOCR model name |

Examples:

```bash
BACKEND_PORT=8080 bash scripts/start.sh backend       # backend on 8080
PPOCR_GPU_ID=1 bash scripts/start.sh ppocr-server     # pin OCR server to GPU 1
FRONTEND_PORT=3000 bash scripts/start.sh frontend     # frontend on 3000
```

### Shutdown

`Ctrl+C` triggers a graceful shutdown: SIGTERM → wait up to 20s for the lifespan to finish OCR / vLLM cleanup → SIGKILL fallback. Press `Ctrl+C` twice to force-kill all child processes immediately.

OCR engines are managed on demand by `EngineManager`: after the user selects an engine and submits a task in the frontend, the backend automatically starts the corresponding worker (including ppocr-server). When switching engines, the old engine's GPU resources are released automatically.

> If `http_proxy` is set on the system, accessing localhost requires `export no_proxy="localhost,127.0.0.1"` first.

## Configuration

### LLM Integration

LLM refinement runs through [litellm](https://docs.litellm.ai/) and supports two providers: **cloud** and **local**.

#### Mode A: Cloud (default)

Targets any OpenAI-compatible service such as OpenAI / GLM / Claude / Gemini / proxy gateways. Create a `.env` in the project root:

```bash
# Pick whichever fits your model (litellm picks the right key by model name)
OPENAI_API_KEY=sk-xxx
GLM_API_KEY=sk-xxx
GEMINI_API_KEY=sk-xxx

# When routing through a proxy, set the base too
OPENAI_API_BASE=https://your-proxy/v1
```

Cloud mode also issues an extra LLM call for PII entity detection (person/org names) on top of regex redaction.

#### Mode B: Local (data never leaves the machine)

Hook up any OpenAI-compatible local server, for example:

| Backend | Sample command | api_base |
|---------|---------------|----------|
| ollama | `ollama serve` + `ollama pull qwen2.5:14b` | `http://localhost:11434/v1` |
| vLLM | `vllm serve Qwen/Qwen2.5-14B-Instruct --port 8001` | `http://localhost:8001/v1` |
| llama.cpp | `llama-server -m model.gguf --port 8080` | `http://localhost:8080/v1` |

No `.env` API key is required (leave empty or set anything). In local mode the LLM-based PII entity detection is skipped, only regex redaction runs, and **no data is sent to any external service**.

#### Where to set the provider

- **Frontend UI**: Task form → expand "LLM Refinement Settings" → "Provider" radio (Cloud API / Local Service); the same panel hosts `Model Name` / `API Base URL` / `API Key`, with an optional "Remember config" checkbox that persists to localStorage
- **REST API**: pass it under the `llm` field in the request body (see examples below)
- **Config file**: tweak the defaults in `backend/docrestore/pipeline/config.py::LLMConfig`, or inject via yaml

### Other Runtime Settings

Controlled via `PipelineConfig` (`backend/docrestore/pipeline/config.py`, pydantic BaseModel):

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
# Minimal create-task (uses LLM defaults from .env / yaml)
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"image_dir": "/path/to/images", "output_dir": "/path/to/output"}'

# Pin a cloud LLM
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "image_dir": "/path/to/images",
    "output_dir": "/path/to/output",
    "llm": {
      "provider": "cloud",
      "model": "openai/gpt-4o-mini",
      "api_base": "https://your-proxy/v1",
      "api_key": "sk-xxx"
    }
  }'

# Switch to a local LLM (ollama example: API key may be omitted)
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "image_dir": "/path/to/images",
    "output_dir": "/path/to/output",
    "llm": {
      "provider": "local",
      "model": "openai/qwen2.5:14b",
      "api_base": "http://localhost:11434/v1"
    }
  }'

# Download zip results
curl -O http://localhost:8000/api/v1/tasks/{task_id}/download
```

> Keep the `openai/` prefix in `model`: local services all speak the OpenAI schema, and the prefix prevents litellm from raising `LLM Provider NOT provided`. The backend's `_normalize_model_id` also adds it automatically when `api_base` is non-empty.

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
