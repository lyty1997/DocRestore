# DocRestore

将一组文档屏幕拍摄照片还原为原文格式的 Markdown 文档。

**处理流程**：照片 → OCR → 清洗 → 去重合并 → PII 脱敏（可选） → LLM 精修 → 缺口补充 → Markdown 输出

## 环境要求

- Linux（推荐 Ubuntu 20.04+）
- Python 3.11+，Node.js 18+
- NVIDIA GPU（运行 OCR 引擎；DeepSeek-OCR-2 需 ≥ 16GB 显存）
- LLM API Key（litellm 兼容：OpenAI / GLM / Claude / Gemini 等）

## 安装

```bash
git clone <repo-url> && cd docrestore

# 1. 后端环境（必需，无 GPU 依赖）
bash scripts/setup_backend.sh

# 2. OCR 引擎环境（至少安装一个）
bash scripts/setup_paddle_ocr.sh      # PaddleOCR（推荐）
bash scripts/setup_deepseek_ocr.sh    # DeepSeek-OCR-2（备用）

# 3. 前端
cd frontend && npm install && cd ..
```

四个 conda 环境各司其职（详见 [docs/deployment.md](docs/deployment.md)）：

| 环境 | 用途 | GPU |
|------|------|-----|
| `docrestore` | 后端服务（FastAPI/litellm） | 否 |
| `ppocr_vlm` | PaddleOCR genai_server | 是 |
| `ppocr_client` | PaddleOCR worker | 否 |
| `deepseek_ocr` | DeepSeek-OCR-2 worker | 是 |

## 启动

```bash
# 一键启动（后端 + 前端）
bash scripts/start.sh all

# 查看完整帮助
bash scripts/start.sh --help
```

访问前端 http://localhost:5173（后端 API 在 http://0.0.0.0:8000/api/v1）。

### 启动模式

| 模式 | 说明 |
|------|------|
| `all`（默认） | 同时启动后端 + 前端，等后端 lifespan 就绪后再拉前端 |
| `backend` | 仅启动后端（uvicorn + `docrestore.api.app`） |
| `frontend` | 仅启动前端（Vite dev server） |
| `ppocr-server` | 仅启动 PaddleOCR `genai_server`（vLLM 后端，独立进程） |
| `-h` / `--help` | 显示帮助 |

### 环境变量

可在命令前导出覆盖默认值：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKEND_HOST` | `0.0.0.0` | 后端监听地址 |
| `BACKEND_PORT` | `8000` | 后端监听端口 |
| `FRONTEND_PORT` | `5173` | Vite dev server 端口 |
| `PPOCR_GPU_ID` | 留空 | 绑定 GPU 编号；留空则不导出 `CUDA_VISIBLE_DEVICES`，由 vLLM 自动枚举 + `gpu_detect.pick_best_gpu` 按显存挑卡 |
| `PPOCR_PORT` | `8119` | PaddleOCR `genai_server` 端口 |
| `PPOCR_MODEL` | `PaddleOCR-VL-1.5-0.9B` | PaddleOCR 模型名 |

示例：

```bash
BACKEND_PORT=8080 bash scripts/start.sh backend       # 后端改 8080
PPOCR_GPU_ID=1 bash scripts/start.sh ppocr-server     # 绑 GPU 1 启 OCR server
FRONTEND_PORT=3000 bash scripts/start.sh frontend     # 前端改 3000
```

### 退出

`Ctrl+C` 触发优雅关闭：SIGTERM → 最长 20s 等 lifespan 跑完 OCR / vLLM 清理 → SIGKILL 兜底。连按两次 `Ctrl+C` 立即强杀所有子进程。

OCR 引擎由 `EngineManager` 按需管理：前端选择引擎并提交任务后，后端自动启动对应 worker（含 ppocr-server），切换引擎时自动释放旧引擎 GPU。

> 若系统设置了 `http_proxy`，访问 localhost 需先 `export no_proxy="localhost,127.0.0.1"`。

## 配置

### LLM 接入

LLM 精修通过 [litellm](https://docs.litellm.ai/) 调用，支持 **云端** 和 **本地** 两种 provider。

#### 模式 A：云端（默认）

调用 OpenAI / GLM / Claude / Gemini / 中转站等任意 OpenAI 兼容服务。在项目根创建 `.env`：

```bash
# 选其一即可（litellm 按 model 名自动选 key）
OPENAI_API_KEY=sk-xxx
GLM_API_KEY=sk-xxx
GEMINI_API_KEY=sk-xxx

# 走中转站时另外指定 base
OPENAI_API_BASE=https://your-proxy/v1
```

云端模式额外会调一次 LLM 做 PII 实体识别（人名/机构名），与 regex 脱敏叠加。

#### 模式 B：本地（数据不出本地）

接入任意 OpenAI 兼容的本地服务，例如：

| 后端 | 启动示例 | api_base |
|------|---------|----------|
| ollama | `ollama serve` + `ollama pull qwen2.5:14b` | `http://localhost:11434/v1` |
| vLLM | `vllm serve Qwen/Qwen2.5-14B-Instruct --port 8001` | `http://localhost:8001/v1` |
| llama.cpp | `llama-server -m model.gguf --port 8080` | `http://localhost:8080/v1` |

不需要在 `.env` 里设 API Key（留空或随便填即可）。本地模式下 PII 实体识别会跳过，只跑 regex 脱敏，**数据不会发送到任何外部服务**。

#### 在哪里配 provider

- **前端 UI**：任务表单 → "LLM 精修配置" 展开 → "Provider" radio（云端 API / 本地服务），同行下方填 `Model Name` / `API Base URL` / `API Key`，可勾"记住配置"持久化到 localStorage
- **REST API**：在请求体的 `llm` 字段里传，示例见下方
- **配置文件**：`backend/docrestore/pipeline/config.py::LLMConfig` 改默认值，或挂 yaml 注入

### 其它运行时配置

通过 `PipelineConfig`（`backend/docrestore/pipeline/config.py`，pydantic BaseModel）控制：

- `OCRConfig` — 引擎选择、GPU id、图片预处理、侧栏过滤
- `DedupConfig` — 行级模糊匹配阈值、重叠上下文行数
- `LLMConfig` — provider（cloud/local）、模型、API 地址、分段大小、截断检测、全局并发上限（`max_concurrent_requests`）
- `OutputConfig` / `PIIConfig` — 输出格式、PII 脱敏

字段说明详见 [docs/backend/data-models.md](docs/zh/backend/data-models.md)。

## 使用

### Web 前端

启动后访问 http://localhost:5173：
- 上传图片或选择服务器路径创建任务
- WebSocket 实时进度（OCR / 清洗 / 精修 / 输出）
- Markdown 预览（多文档子文档切换）+ 人工精修 + zip 下载
- 任务历史：分页、状态筛选、取消 / 重试 / 删除

### 命令行（端到端）

```bash
conda activate docrestore && source .env
python scripts/run_e2e.py \
  --input test_images \
  --output output/test \
  --ocr-model paddle-ocr/ppocr-v4
```

### REST API

完整接口契约见 [docs/backend/api.md](docs/zh/backend/api.md)，示例：

```bash
# 最小创建任务（用 .env / yaml 里的 LLM 默认值）
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"image_dir": "/path/to/images", "output_dir": "/path/to/output"}'

# 指定云端 LLM
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

# 切到本地 LLM（ollama 示例：API Key 可省略）
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

# 下载 zip 结果
curl -O http://localhost:8000/api/v1/tasks/{task_id}/download
```

> `model` 名前缀建议保留 `openai/`：本地服务都走 OpenAI schema，加上前缀避免 litellm 因不识别厂商而报 `LLM Provider NOT provided`。后端 `_normalize_model_id` 在 `api_base` 非空时也会自动兜底。

## 输出说明

```
output_dir/
├── {文档标题}/              # 每个文档组一个子目录（单文档时退化为一组）
│   ├── document.md         # 还原后的 Markdown
│   └── images/             # 裁剪插图
└── {stem}_OCR/             # 各照片 OCR 中间结果（原始文本 + grounding 裁剪图）
```

无法自动补全的内容缺口会在 Markdown 中插入 GAP 标记，附带原图文件名。

## 开发与测试

```bash
conda activate docrestore

# 后端检查
ruff check backend/
mypy --strict backend/docrestore/
pytest

# 前端检查与测试
cd frontend && npm run lint && npm test
```

## 项目结构

```
docrestore/
├── backend/docrestore/   # 后端（api / ocr / processing / llm / privacy / pipeline / persistence / output）
├── frontend/             # 前端（React 19 + Vite + TypeScript strict）
├── scripts/              # 安装 / 启动 / 端到端脚本
├── tests/                # 后端测试
└── docs/                 # 设计文档（架构 / 部署 / 后端 / 前端 / 进度）
```

## 文档 / Documentation

- [中文文档](docs/zh/README.md) | [English Docs](docs/en/README.md)
- [系统架构](docs/zh/architecture.md) | [Architecture](docs/en/architecture.md)
- [部署指南](docs/zh/deployment.md) | [Deployment](docs/en/deployment.md)
- [后端文档](docs/zh/backend/README.md) | [Backend](docs/en/backend/README.md)
- [前端文档](docs/zh/frontend/README.md) | [Frontend](docs/en/frontend/README.md)
- [开发进度](docs/zh/progress.md)

## License

Apache License 2.0
