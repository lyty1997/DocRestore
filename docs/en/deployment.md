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

# DocRestore Deployment Guide

## 1. Requirements

### 1.1 Hardware
- GPU: NVIDIA GPU (VRAM >= 8 GB for PaddleOCR; >= 16 GB for DeepSeek-OCR-2)
- CPU: 4+ cores
- RAM: 16 GB or more
- Disk: 50 GB or more (models + data)

### 1.2 Software
- OS: Linux (Ubuntu 20.04+ recommended)
- Python: 3.11+
- Node.js: 18+ (frontend)
- CUDA: 12.8 (PaddleOCR default) or 11.8 (DeepSeek-OCR-2)
- Conda: Miniconda / Anaconda

## 2. Quick Start

### 2.1 Setting Up the Environment

**Step 1: Backend environment (required)**

```bash
git clone <repo-url> && cd docrestore

# Install the lightweight backend environment (no GPU dependencies)
bash scripts/setup_backend.sh
```

Creates the `docrestore` conda environment containing only backend dependencies such as FastAPI, uvicorn, and litellm.

**Step 2: OCR engine environment (install at least one)**

**PaddleOCR (recommended)**:

```bash
# Install both server and client conda environments
bash scripts/setup_paddle_ocr.sh

# Install server only (for a dedicated GPU machine)
bash scripts/setup_paddle_ocr.sh --server-only

# Install client only (server runs on another machine)
bash scripts/setup_paddle_ocr.sh --client-only
```

Environments created:
- `ppocr_vlm`: genai_server (VLM inference, uses GPU)
- `ppocr_client`: worker (layout analysis + server calls)

**DeepSeek-OCR-2 (fallback)**:

```bash
# Install OCR engine + vendor + model
bash scripts/setup_deepseek_ocr.sh

# Skip model download (download manually)
bash scripts/setup_deepseek_ocr.sh --skip-model
```

Creates the `deepseek_ocr` conda environment (Python 3.12) with PyTorch 2.6.0 + vLLM 0.8.5 + flash-attn 2.7.3.

**Four-environment overview**:

| Environment | Install Script | Purpose | GPU |
|-------------|---------------|---------|-----|
| `docrestore` | `setup_backend.sh` | Backend service (FastAPI/uvicorn/litellm) | No |
| `ppocr_vlm` | `setup_paddle_ocr.sh` | PaddleOCR genai_server | Yes |
| `ppocr_client` | `setup_paddle_ocr.sh` | PaddleOCR worker | No |
| `deepseek_ocr` | `setup_deepseek_ocr.sh` | DeepSeek-OCR-2 worker | Yes |

### 2.2 Installing the Frontend

```bash
cd frontend && npm install
```

### 2.3 Starting the Services

```bash
# Start both backend and frontend
bash scripts/start.sh all
```

OCR engines are managed on demand by the EngineManager: the corresponding engine (including PaddleOCR's ppocr-server) is started automatically when the first task is submitted -- no manual startup required. When the engine is switched from the frontend, the backend automatically releases the old engine's GPU and starts the new engine.

```bash
# You can also start them separately
bash scripts/start.sh backend   # Backend API: http://0.0.0.0:8000
bash scripts/start.sh frontend  # Frontend:    http://localhost:5173
```

> **Manually starting ppocr-server (optional)**: If you prefer not to use the EngineManager for automatic management, you can still start it manually:
> ```bash
> bash scripts/start.sh ppocr-server
> ```

After the services are running:
- Backend API: `http://0.0.0.0:8000/api/v1`
- Frontend: `http://localhost:5173`
- PaddleOCR server: `http://localhost:8119` (started automatically by EngineManager, or manually)

## 3. Environment Variables

### 3.1 Startup Script Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_HOST` | `0.0.0.0` | Backend listen address |
| `BACKEND_PORT` | `8000` | Backend listen port |
| `FRONTEND_PORT` | `5173` | Frontend dev server port |
| `PPOCR_GPU_ID` | empty (auto) | GPU used by PaddleOCR server; when empty, `gpu_detect.pick_best_gpu` selects the one with the most VRAM |
| `PPOCR_PORT` | `8119` | PaddleOCR server port |
| `PPOCR_MODEL` | `PaddleOCR-VL-1.5-0.9B` | PaddleOCR model name |

### 3.2 PaddleOCR Installation Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PPOCR_GPU_MEMORY_UTIL` | `0.85` | GPU memory utilization |
| `CUDA_VERSION` | `12.8` | CUDA toolkit version |
| `PADDLE_GPU_VERSION` | `3.3.0` | paddlepaddle-gpu version |
| `FLASH_ATTN_VERSION` | `2.8.2` | flash-attn version |

### 3.3 DeepSeek-OCR-2 Installation Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CUDA_TAG` | `cu118` | CUDA version tag |
| `VLLM_WHL` | -- | Local vllm whl path (skips download) |

### 3.4 LLM API Configuration

Create a `.env` file:

```bash
# LLM API Key (choose based on the model you use)
GEMINI_API_KEY=sk-xxx
OPENAI_API_KEY=sk-xxx
GLM_API_KEY=sk-xxx

# Or use a custom API base
OPENAI_API_BASE=https://your-proxy.com/v1
```

## 4. OCR Engine Configuration

### 4.1 Engine Selection

Specified via `OCRConfig.model`. Supported identifiers:

| Identifier | Engine | Description |
|-----------|--------|-------------|
| `paddle-ocr/ppocr-v4` (default) | PaddleOCR | Lightweight, separate conda environment |
| `paddle-ocr` | PaddleOCR | Short form |
| `deepseek/ocr-2` | DeepSeek-OCR-2 | High-accuracy grounding OCR |
| `deepseek` | DeepSeek-OCR-2 | Short form |

**GPU selection**: `OCRConfig.gpu_id` (default `None`) controls which GPU both engines use. When unset, the backend calls `docrestore.ocr.gpu_detect.pick_best_gpu()` before starting ppocr-server and picks the GPU with the most VRAM. The frontend task form fetches the list via `GET /api/v1/gpus` so users can override it, and `PPOCR_GPU_ID` can pin it explicitly.

### 4.2 PaddleOCR Configuration

PaddleOCR uses separate conda environments, split into server (VLM inference) and client (layout analysis + server calls).

**EngineManager automatic management** (recommended):

On backend startup the system automatically detects the python paths for the `ppocr_client` and `ppocr_vlm` conda environments. When the first PaddleOCR task is submitted, the EngineManager automatically starts the ppocr-server subprocess and waits for it to become ready.

**Manually starting the server** (optional):

```bash
# Using start.sh
bash scripts/start.sh ppocr-server

# Custom GPU and port
PPOCR_GPU_ID=0 PPOCR_PORT=9119 bash scripts/start.sh ppocr-server
```

**Key OCRConfig fields**:

| Field | Description |
|-------|-------------|
| `paddle_python` | Python path of the ppocr_client conda environment (auto-detected) |
| `paddle_server_python` | Python of the ppocr_vlm conda environment (used by EngineManager to start the server, auto-detected) |
| `paddle_server_url` | Server URL (auto-configured as `http://localhost:{port}/v1`) |
| `paddle_server_port` | ppocr-server port (default 8119) |
| `paddle_server_startup_timeout` | Server startup timeout in seconds (default 300) |
| `paddle_server_host` / `paddle_server_port` / `paddle_server_api_version` | Auto-assembled into `paddle_server_url` (default `http://localhost:8119/v1`) |
| `paddle_server_model_name` | Server model name (default `PaddleOCR-VL-1.5-0.9B`) |
| `paddle_ocr_timeout` | Per-image OCR timeout in seconds (default 300) |
| `paddle_restart_interval` | Restart worker every N images (recommend 0 in server mode) |

> When `paddle_server_python` is empty, automatic server startup is skipped and the engine falls back to local inference mode.

### 4.3 DeepSeek-OCR-2 Configuration

DeepSeek-OCR-2 also runs as a subprocess worker in a separate conda environment, started on demand by the EngineManager.

**Key OCRConfig fields**:

| Field | Description |
|-------|-------------|
| `deepseek_python` | Python path of the deepseek_ocr conda environment (auto-detected) |
| `deepseek_ocr_timeout` | Per-image OCR timeout in seconds (default 600; DeepSeek inference is slower) |
| `model_path` | Model directory path (default `models/DeepSeek-OCR-2`) |
| `gpu_memory_utilization` | GPU memory utilization ratio (default 0.75) |

The model must be downloaded manually:

```bash
huggingface-cli download deepseek-ai/DeepSeek-OCR-2 \
  --local-dir models/DeepSeek-OCR-2
```

### 4.4 LLM Configuration

```yaml
llm:
  provider: "cloud"          # "cloud" or "local"
  model: "openai/gemini-3-flash-preview-nothinking"
  api_base: "https://poloai.top/v1"
  api_key: ""                # When empty, auto-reads from environment variables
```

## 5. Verifying the Installation

### 5.1 Backend Health Check

```bash
curl http://127.0.0.1:8000/health
# Expected output: {"status": "ok"}
```

### 5.2 Running Tests

```bash
# Backend tests
pytest

# Frontend tests
cd frontend && npm test
```

### 5.3 End-to-End Test

```bash
conda activate docrestore
source .env

# Using PaddleOCR (default)
python scripts/run_e2e.py \
  --input test_images \
  --output output/test \
  --paddle-server-url http://localhost:8119/v1

# Using DeepSeek-OCR-2
python scripts/run_e2e.py \
  --input test_images \
  --output output/test \
  --ocr-model deepseek/ocr-2
```

## 6. Production Deployment

### 6.1 Backend (systemd)

Create `/etc/systemd/system/docrestore.service`:

```ini
[Unit]
Description=DocRestore API
After=network.target

[Service]
Type=simple
User=docrestore
WorkingDirectory=/path/to/docrestore
Environment="PATH=/path/to/conda/envs/docrestore/bin"
ExecStart=/path/to/conda/envs/docrestore/bin/python -m uvicorn docrestore.api.app:create_app --factory --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

The ppocr-server is automatically managed by the EngineManager, so no additional systemd service is needed. If you need to deploy ppocr-server independently (e.g., on a dedicated GPU machine), you can create an extra service:

```ini
[Unit]
Description=PaddleOCR GenAI Server
After=network.target

[Service]
Type=simple
User=docrestore
Environment="PATH=/path/to/conda/envs/ppocr_vlm/bin"
# Pin to a specific GPU if needed (leave unset to let vLLM enumerate every visible GPU)
# Environment="CUDA_VISIBLE_DEVICES=0"
ExecStart=/path/to/conda/envs/ppocr_vlm/bin/paddleocr genai_server --model_name PaddleOCR-VL-1.5-0.9B --backend vllm --port 8119
Restart=always

[Install]
WantedBy=multi-user.target
```

Start the services:

```bash
# Backend only (ppocr-server is managed automatically by EngineManager)
sudo systemctl enable docrestore
sudo systemctl start docrestore

# If deploying ppocr-server independently
sudo systemctl enable docrestore docrestore-ppocr
sudo systemctl start docrestore-ppocr docrestore
```

### 6.2 Frontend (Nginx)

Build the frontend:

```bash
cd frontend && npm run build
```

Nginx configuration:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    root /path/to/docrestore/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## 7. Troubleshooting

### 7.1 GPU Issues

```bash
nvidia-smi                                              # GPU availability
nvcc --version                                          # CUDA version
python -c "import torch; print(torch.cuda.is_available())"  # PyTorch GPU support
```

### 7.2 Dependency Conflicts

In the DeepSeek-OCR-2 environment, vllm and transformers have a version conflict:
- vllm 0.8.5 requires transformers >= 4.51
- DeepSeek-OCR-2 requires transformers == 4.46.3
- Solution: Install vllm first, then force-downgrade transformers (`setup_deepseek_ocr.sh` handles this)

PaddleOCR and DeepSeek-OCR-2 have incompatible dependencies, which is why separate conda environments are used for isolation.

### 7.3 Proxy Issues

If `http_proxy` is set on the system, curl requests to localhost may go through the proxy and time out:

```bash
# Option 1: Add flag to curl
curl --noproxy localhost http://127.0.0.1:8000/health

# Option 2: Set environment variable (recommended: add to .bashrc)
export no_proxy="localhost,127.0.0.1"
```

### 7.4 Viewing Logs

```bash
# systemd deployment
journalctl -u docrestore -f

# Development mode
tail -f logs/docrestore.log
```

## 8. Related Documentation

- [System Architecture](architecture.md)
- [Backend Architecture](backend/README.md)
- [Frontend Architecture](frontend/README.md)
