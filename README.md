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
```

访问前端 http://localhost:5173（后端 API 在 http://0.0.0.0:8000/api/v1）。

OCR 引擎由 `EngineManager` 按需管理：前端选择引擎并提交任务后，后端自动启动对应 worker（含 ppocr-server），切换引擎时自动释放旧引擎 GPU。

> 若系统设置了 `http_proxy`，访问 localhost 需先 `export no_proxy="localhost,127.0.0.1"`。

## 配置

在项目根创建 `.env` 配置 LLM API Key：

```bash
GEMINI_API_KEY=sk-xxx
# 或 OPENAI_API_KEY / GLM_API_KEY
# 也可指向中转站：OPENAI_API_BASE=https://your-proxy/v1
```

运行时配置通过 `PipelineConfig` 控制（`backend/docrestore/pipeline/config.py`，pydantic BaseModel）：

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
# 创建任务
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"image_dir": "/path/to/images", "output_dir": "/path/to/output"}'

# 下载 zip 结果
curl -O http://localhost:8000/api/v1/tasks/{task_id}/download
```

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
