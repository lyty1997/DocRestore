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

# DocRestore 部署指南

## 1. 环境要求

### 1.1 硬件
- GPU：NVIDIA GPU（显存 ≥ 8GB，PaddleOCR；≥ 16GB，DeepSeek-OCR-2）
- CPU：4 核以上
- 内存：16GB 以上
- 磁盘：50GB 以上（模型 + 数据）

### 1.2 软件
- 操作系统：Linux（推荐 Ubuntu 20.04+）
- Python：3.11+
- Node.js：18+（前端）
- CUDA：12.8（PaddleOCR 默认）或 11.8（DeepSeek-OCR-2）
- Conda：Miniconda / Anaconda

## 2. 快速开始

### 2.1 安装环境

**步骤 1：后端环境（必需）**

```bash
git clone <repo-url> && cd docrestore

# 安装轻量级后端环境（无 GPU 依赖）
bash scripts/setup_backend.sh
```

创建 `docrestore` conda 环境，仅含 FastAPI/uvicorn/litellm 等后端依赖。

**步骤 2：OCR 引擎环境（至少安装一个）**

**PaddleOCR（推荐）**：

```bash
# 安装 server + client 两个 conda 环境
bash scripts/setup_paddle_ocr.sh

# 仅安装 server（适合专用 GPU 机器）
bash scripts/setup_paddle_ocr.sh --server-only

# 仅安装 client（server 在其他机器上）
bash scripts/setup_paddle_ocr.sh --client-only
```

创建的环境：
- `ppocr_vlm`：genai_server（VLM 推理，占用 GPU）
- `ppocr_client`：worker（布局分析 + 调用 server）

**DeepSeek-OCR-2（备用）**：

```bash
# 安装 OCR 引擎 + vendor + 模型
bash scripts/setup_deepseek_ocr.sh

# 跳过模型下载（需手动下载）
bash scripts/setup_deepseek_ocr.sh --skip-model
```

创建 `deepseek_ocr` conda 环境（Python 3.12），安装 PyTorch 2.6.0 + vLLM 0.8.5 + flash-attn 2.7.3。

**四环境总览**：

| 环境 | 安装脚本 | 用途 | GPU |
|------|---------|------|-----|
| `docrestore` | `setup_backend.sh` | 后端服务（FastAPI/uvicorn/litellm） | 否 |
| `ppocr_vlm` | `setup_paddle_ocr.sh` | PaddleOCR genai_server | 是 |
| `ppocr_client` | `setup_paddle_ocr.sh` | PaddleOCR worker | 否 |
| `deepseek_ocr` | `setup_deepseek_ocr.sh` | DeepSeek-OCR-2 worker | 是 |

### 2.2 安装前端

```bash
cd frontend && npm install
```

### 2.3 启动服务

```bash
# 一键启动后端 + 前端
bash scripts/start.sh all
```

OCR 引擎由 EngineManager 按需管理：首次提交任务时自动启动对应引擎（包括 PaddleOCR 的 ppocr-server），无需手动启动。前端切换引擎后，后端自动释放旧引擎 GPU 并启动新引擎。

```bash
# 也可分别启动
bash scripts/start.sh backend   # 后端 API：http://0.0.0.0:8000
bash scripts/start.sh frontend  # 前端页面：http://localhost:5173
```

> **手动启动 ppocr-server（可选）**：如果不使用 EngineManager 自动管理，仍可手动启动：
> ```bash
> bash scripts/start.sh ppocr-server
> ```

服务启动后：
- 后端 API：`http://0.0.0.0:8000/api/v1`
- 前端页面：`http://localhost:5173`
- PaddleOCR server：`http://localhost:8119`（EngineManager 自动启动，或手动启动）

## 3. 环境变量配置

### 3.1 启动脚本变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKEND_HOST` | `0.0.0.0` | 后端监听地址 |
| `BACKEND_PORT` | `8000` | 后端监听端口 |
| `FRONTEND_PORT` | `5173` | 前端开发服务器端口 |
| `PPOCR_GPU_ID` | 空（自动） | PaddleOCR server 使用的 GPU；留空时由 `gpu_detect.pick_best_gpu` 选显存最大的一张 |
| `PPOCR_PORT` | `8119` | PaddleOCR server 端口 |
| `PPOCR_MODEL` | `PaddleOCR-VL-1.5-0.9B` | PaddleOCR 模型名 |

### 3.2 PaddleOCR 安装变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PPOCR_GPU_MEMORY_UTIL` | `0.85` | 显存利用率 |
| `CUDA_VERSION` | `12.8` | CUDA 工具链版本 |
| `PADDLE_GPU_VERSION` | `3.3.0` | paddlepaddle-gpu 版本 |
| `FLASH_ATTN_VERSION` | `2.8.2` | flash-attn 版本 |

### 3.3 DeepSeek-OCR-2 安装变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CUDA_TAG` | `cu118` | CUDA 版本标签 |
| `VLLM_WHL` | — | 本地 vllm whl 路径（跳过下载） |

### 3.4 LLM API 配置

创建 `.env` 文件：

```bash
# LLM API Key（根据所用模型选择）
GEMINI_API_KEY=sk-xxx
OPENAI_API_KEY=sk-xxx
GLM_API_KEY=sk-xxx

# 或使用自定义 API base
OPENAI_API_BASE=https://your-proxy.com/v1
```

## 4. OCR 引擎配置

### 4.1 引擎选择

通过 `OCRConfig.model` 指定，支持以下标识符：

| 标识符 | 引擎 | 说明 |
|--------|------|------|
| `paddle-ocr/ppocr-v4`（默认） | PaddleOCR | 轻量级，独立 conda 环境 |
| `paddle-ocr` | PaddleOCR | 简写形式 |
| `deepseek/ocr-2` | DeepSeek-OCR-2 | 高精度 grounding OCR |
| `deepseek` | DeepSeek-OCR-2 | 简写形式 |

**GPU 选择**：`OCRConfig.gpu_id`（默认 `None`）统一控制两个引擎使用的 GPU。未显式指定时，后端在启动 ppocr-server 前调用 `docrestore.ocr.gpu_detect.pick_best_gpu()`，按显存降序自动挑选可用 GPU；前端任务表单会调用 `GET /api/v1/gpus` 拉取列表并允许用户在下拉中切换；也可通过环境变量 `PPOCR_GPU_ID` 显式指定。

### 4.2 PaddleOCR 配置

PaddleOCR 使用独立 conda 环境，分为 server（VLM 推理）和 client（布局分析 + 调用 server）。

**EngineManager 自动管理**（推荐）：

后端启动时自动检测 ppocr_client 和 ppocr_vlm 两个 conda 环境的 python 路径。首次提交 PaddleOCR 任务时，EngineManager 自动启动 ppocr-server 子进程并等待就绪。

**手动启动 server**（可选）：

```bash
# 使用 start.sh
bash scripts/start.sh ppocr-server

# 自定义 GPU 和端口
PPOCR_GPU_ID=0 PPOCR_PORT=9119 bash scripts/start.sh ppocr-server
```

**OCRConfig 关键字段**：

| 字段 | 说明 |
|------|------|
| `paddle_python` | ppocr_client conda 环境的 python 路径（自动检测） |
| `paddle_server_python` | ppocr_vlm conda 环境的 python（EngineManager 启动 server 用，自动检测） |
| `paddle_server_url` | server URL（自动配置为 `http://localhost:{port}/v1`） |
| `paddle_server_port` | ppocr-server 端口（默认 8119） |
| `paddle_server_startup_timeout` | server 启动超时秒数（默认 300） |
| `paddle_server_host` / `paddle_server_port` / `paddle_server_api_version` | 自动拼接 `paddle_server_url`（默认 `http://localhost:8119/v1`） |
| `paddle_server_model_name` | server 模型名（默认 `PaddleOCR-VL-1.5-0.9B`） |
| `paddle_ocr_timeout` | 单张 OCR 超时秒数（默认 300） |
| `paddle_restart_interval` | 每 N 张重启 worker（server 模式建议设 0） |

> `paddle_server_python` 为空时跳过 server 自动启动，回退到本地推理模式。

### 4.3 DeepSeek-OCR-2 配置

DeepSeek-OCR-2 同样以子进程 worker 运行在独立 conda 环境中，由 EngineManager 按需启动。

**OCRConfig 关键字段**：

| 字段 | 说明 |
|------|------|
| `deepseek_python` | deepseek_ocr conda 环境的 python 路径（自动检测） |
| `deepseek_ocr_timeout` | 单张 OCR 超时秒数（默认 600，DeepSeek 推理较慢） |
| `model_path` | 模型目录路径（默认 `models/DeepSeek-OCR-2`） |
| `gpu_memory_utilization` | GPU 显存占用比例（默认 0.75） |

模型需手动下载：

```bash
huggingface-cli download deepseek-ai/DeepSeek-OCR-2 \
  --local-dir models/DeepSeek-OCR-2
```

### 4.4 LLM 配置

```yaml
llm:
  provider: "cloud"          # "cloud" 或 "local"
  model: "openai/gemini-3-flash-preview-nothinking"
  api_base: "https://poloai.top/v1"
  api_key: ""                # 为空时从环境变量自动读取
```

## 5. 验证安装

### 5.1 后端健康检查

```bash
curl http://127.0.0.1:8000/health
# 预期输出：{"status": "ok"}
```

### 5.2 运行测试

```bash
# 后端测试
pytest

# 前端测试
cd frontend && npm test
```

### 5.3 端到端测试

```bash
conda activate docrestore
source .env

# 使用 PaddleOCR（默认）
python scripts/run_e2e.py \
  --input test_images \
  --output output/test \
  --paddle-server-url http://localhost:8119/v1

# 使用 DeepSeek-OCR-2
python scripts/run_e2e.py \
  --input test_images \
  --output output/test \
  --ocr-model deepseek/ocr-2
```

## 6. 生产部署

### 6.1 后端（systemd）

创建 `/etc/systemd/system/docrestore.service`：

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

ppocr-server 由 EngineManager 自动管理，无需额外 systemd service。如果需要独立部署 ppocr-server（例如在专用 GPU 机器上），可额外创建一个 service：

```ini
[Unit]
Description=PaddleOCR GenAI Server
After=network.target

[Service]
Type=simple
User=docrestore
Environment="PATH=/path/to/conda/envs/ppocr_vlm/bin"
# 需要固定到某张 GPU 再设置（留空则让 vLLM 自行枚举所有可见 GPU）
# Environment="CUDA_VISIBLE_DEVICES=0"
ExecStart=/path/to/conda/envs/ppocr_vlm/bin/paddleocr genai_server --model_name PaddleOCR-VL-1.5-0.9B --backend vllm --port 8119
Restart=always

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
# 仅后端（ppocr-server 由 EngineManager 自动管理）
sudo systemctl enable docrestore
sudo systemctl start docrestore

# 如需独立部署 ppocr-server
sudo systemctl enable docrestore docrestore-ppocr
sudo systemctl start docrestore-ppocr docrestore
```

### 6.2 前端（Nginx）

构建前端：

```bash
cd frontend && npm run build
```

Nginx 配置：

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

## 7. 故障排查

### 7.1 GPU 相关

```bash
nvidia-smi                                              # GPU 可用性
nvcc --version                                          # CUDA 版本
python -c "import torch; print(torch.cuda.is_available())"  # PyTorch GPU 支持
```

### 7.2 依赖冲突

DeepSeek-OCR-2 环境中 vllm 和 transformers 版本冲突：
- vllm 0.8.5 要求 transformers ≥ 4.51
- DeepSeek-OCR-2 要求 transformers == 4.46.3
- 解决：先装 vllm，再强制降级 transformers（`setup_deepseek_ocr.sh` 已处理）

PaddleOCR 与 DeepSeek-OCR-2 依赖不兼容，因此使用独立 conda 环境隔离。

### 7.3 代理问题

如果系统设置了 `http_proxy`，curl 访问 localhost 会走代理导致超时：

```bash
# 方式一：curl 加参数
curl --noproxy localhost http://127.0.0.1:8000/health

# 方式二：设置环境变量（建议加到 .bashrc）
export no_proxy="localhost,127.0.0.1"
```

### 7.4 日志查看

```bash
# systemd 部署
journalctl -u docrestore -f

# 开发模式
tail -f logs/docrestore.log
```

## 8. 相关文档

- [系统架构](architecture.md)
- [后端架构](backend/README.md)
- [前端架构](frontend/README.md)
