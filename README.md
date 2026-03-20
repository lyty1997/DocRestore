# DocRestore

将一组文档屏幕拍摄照片还原为原文格式的 Markdown 文档。

处理流程：照片输入 → OCR 识别 → 文本清洗 → 页面去重合并 → LLM 精修 → Markdown 输出

## 环境要求

- Python 3.11+
- NVIDIA GPU（运行 DeepSeek-OCR-2）
- CUDA 11.8（默认，也支持 cu121/cu124）
- LLM API（当前使用 GLM，通过 litellm 调用）

## 安装

```bash
git clone <repo-url> && cd docrestore

# 完整安装（开发依赖 + OCR 引擎 + vendor）
./scripts/setup.sh

# 仅开发依赖（无 GPU 环境时跳过 OCR）
./scripts/setup.sh --no-ocr
```

安装脚本会自动：
1. 创建 `.venv` 虚拟环境
2. 安装 PyTorch 2.6.0 + torchvision 0.21.0（CUDA 版本）
3. 下载并安装 vllm 0.8.5（从 GitHub releases）
4. 降级 transformers 到 4.46.3（DeepSeek-OCR-2 要求）
5. 克隆 DeepSeek-OCR-2 到 `vendor/`
6. 编译安装 flash-attn 2.7.3

环境变量可覆盖默认行为：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PYTHON_BIN` | Python 解释器路径 | 自动检测 |
| `CUDA_TAG` | CUDA 版本标签 | `cu118` |
| `VLLM_WHL` | 本地 vllm whl 路径（跳过下载） | — |

## 配置

### LLM 模型配置

LLM 精修通过 [litellm](https://docs.litellm.ai/) 调用，支持 OpenAI、GLM、Claude、Gemini 等任意兼容 OpenAI 格式的 API。

在 `.env` 中设置 API Key：

```bash
GLM_API_KEY=sk-your-api-key-here
```

通过 `LLMConfig` 配置模型参数（`src/docrestore/pipeline/config.py`）：

```python
from docrestore.pipeline.config import LLMConfig

# GLM（默认，通过 litellm 路由）
LLMConfig(
    model="openai/glm-5",
    api_base="https://poloai.top/v1",
    api_key="sk-xxx",  # 或通过环境变量 GLM_API_KEY 读取
)

# OpenAI
LLMConfig(
    model="gpt-4o",
    api_key="sk-xxx",  # 或环境变量 OPENAI_API_KEY
)

# 自定义 API 中转站（兼容 OpenAI 格式）
LLMConfig(
    model="openai/your-model",
    api_base="https://your-proxy.example.com/v1",
    api_key="sk-xxx",
)

# 本地部署的模型（如 Ollama、vLLM）
LLMConfig(
    model="openai/your-model-name",
    api_base="http://localhost:11434/v1",
    api_key="dummy",  # 本地模型通常不需要真实 key
)
```

`model` 字段使用 litellm 的模型命名格式：`provider/model-name`。详见 [litellm 支持的模型列表](https://docs.litellm.ai/docs/providers)。

`api_key` 为空时，litellm 会自动从环境变量读取（如 `OPENAI_API_KEY`、`GLM_API_KEY` 等）。

### Pipeline 配置

通过 `PipelineConfig` dataclass 控制（`src/docrestore/pipeline/config.py`）：

- `OCRConfig` — 模型路径、GPU 显存占用、图片预处理参数、循环抑制
- `DedupConfig` — 行级模糊匹配阈值、重叠上下文行数
- `LLMConfig` — 模型名、API 地址、分段大小、重试次数
- `OutputConfig` — 图片格式和质量
- `debug` — 是否保存各阶段中间结果到 `output_dir/debug/`（默认开启）

## 使用

### 脚本方式（端到端）

```bash
source .venv/bin/activate
source .env
python scripts/run_e2e.py
```

默认读取 `test_images/development_guide/` 下的照片，输出到 `output/development_guide/`。

### API 方式

启动服务：

```bash
source .venv/bin/activate
uvicorn docrestore.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

> **注意**：如果系统设置了 `http_proxy` 环境变量，curl 访问 localhost 会走代理导致请求超时无响应。解决方式二选一：
> - curl 加 `--noproxy localhost` 参数
> - 设置环境变量 `export no_proxy="localhost,127.0.0.1"`（建议加到 `.bashrc`）

接口：

```bash
# 创建任务（使用服务默认 LLM 配置）
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"image_dir": "/path/to/images", "output_dir": "/path/to/output"}'

# 创建任务（指定 LLM 模型，覆盖服务默认配置）
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "image_dir": "/path/to/images",
    "output_dir": "/path/to/output",
    "llm": {
      "model": "openai/glm-5",
      "api_key": "sk-xxx"
    }
  }'

# 查询进度
curl http://localhost:8000/api/v1/tasks/{task_id}

# 获取结果
curl http://localhost:8000/api/v1/tasks/{task_id}/result
```

创建任务请求参数：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `image_dir` | string | 是 | 图片目录路径 |
| `output_dir` | string | 否 | 输出目录路径，默认 `/tmp/docrestore_{task_id}` |
| `llm` | object | 否 | 请求级 LLM 配置覆盖，不传则使用服务默认配置 |
| `llm.model` | string | 否 | litellm 模型名（如 `openai/glm-5`、`gpt-4o`） |
| `llm.api_base` | string | 否 | 自定义 API 地址 |
| `llm.api_key` | string | 否 | API Key |
| `llm.max_chars_per_segment` | int | 否 | 分段上限（默认 6000） |

响应示例：

```json
// POST /api/v1/tasks
{"task_id": "a1b2c3d4", "status": "pending"}

// GET /api/v1/tasks/{task_id}
{"task_id": "a1b2c3d4", "status": "processing", "progress": {
  "stage": "ocr", "current": 5, "total": 26, "percent": 19.2,
  "message": "正在 OCR 第 5 张照片..."
}}

// GET /api/v1/tasks/{task_id}/result
{"task_id": "a1b2c3d4", "output_path": "/path/to/output/document.md",
 "markdown": "# 文档标题\n..."}
```

## 输出说明

处理完成后，输出目录包含：

- `document.md` — 还原后的 Markdown 文档
- `images/` — 文档中引用的裁剪图片
- 各照片的 `{stem}_OCR/` 目录 — OCR 中间结果（原始文本 + grounding 裁剪图）

如果存在无法自动补全的内容缺口，文档中会插入 GAP 标记，标注对应的原图文件名。

## 开发与测试

```bash
source .venv/bin/activate

# 代码检查
ruff check src/
mypy --strict src/docrestore/

# 运行测试
pytest

# 安全扫描
bandit -r src/
```

## 项目结构

```
src/docrestore/
├── api/            # FastAPI REST 接口
├── llm/            # LLM 精修（litellm 调用、prompt、分段器）
├── ocr/            # OCR 引擎（DeepSeek-OCR-2 封装、预处理、循环抑制）
├── output/         # 渲染输出
├── pipeline/       # 核心编排器 + 配置 + 任务管理
├── processing/     # 文本清洗 + 页面去重合并
└── models.py       # 数据模型
```
