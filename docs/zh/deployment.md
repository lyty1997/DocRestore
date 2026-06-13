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
| `BACKEND_HOST` | `127.0.0.1` | 后端监听地址。默认仅本机；要让手机/局域网设备访问改 `0.0.0.0`，此时务必配 token（见 §3.5） |
| `BACKEND_PORT` | `8000` | 后端监听端口 |
| `FRONTEND_PORT` | `5173` | 前端开发服务器端口 |
| `PPOCR_GPU_ID` | 空（自动） | PaddleOCR server 使用的 GPU；留空时由 `gpu_detect.pick_best_gpu` 自动推荐 |
| `PPOCR_PORT` | `8119` | PaddleOCR server 端口 |
| `PPOCR_MODEL` | `PaddleOCR-VL-1.6-0.9B` | PaddleOCR 模型名 |

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

LLM 精修走 [litellm](https://docs.litellm.ai/) 调用，支持 **云端** 和 **本地** 两种 provider。

#### 云端模式（默认）

创建 `.env`，按所用模型选 key：

```bash
# LLM API Key（根据所用模型选择，litellm 按 model 名自动选用）
GEMINI_API_KEY=sk-xxx
OPENAI_API_KEY=sk-xxx
GLM_API_KEY=sk-xxx

# 走中转站时另外指定 base
OPENAI_API_BASE=https://your-proxy.com/v1
```

云端模式额外做一次 LLM PII 实体识别（人名/机构名），与 regex 脱敏叠加。

#### 本地模式（数据不出本地）

接入任意 OpenAI 兼容的本地服务，无需 API Key：

| 后端 | 启动示例 | 默认 api_base |
|------|---------|--------------|
| ollama | `ollama serve` + `ollama pull qwen2.5:14b` | `http://localhost:11434/v1` |
| vLLM | `vllm serve Qwen/Qwen2.5-14B-Instruct --port 8001` | `http://localhost:8001/v1` |
| llama.cpp | `llama-server -m model.gguf --port 8080` | `http://localhost:8080/v1` |

本地模式下 `LocalLLMRefiner.detect_pii_entities` 默认返回空 → 跳过 LLM 实体识别，只跑 regex 脱敏；**数据不会发送到任何外部服务**。`.env` 里的 API Key 可留空。

### 3.5 鉴权与网络暴露（安全）

> 安全基线：**服务永不以「未鉴权」状态对外可达**（fail-closed）。这是面向「桌面服务 + 手机配对」形态设计的——手机需从局域网/远程够到桌面，所以不能用「仅绑 loopback」来兜底，改为「默认即自动生成 token，始终强制校验」。

#### Token 三种来源（按优先级）

| 优先级 | 触发条件 | 行为 |
|--------|----------|------|
| 1 | 设置了 `DOCRESTORE_API_TOKEN`（非空） | 用该 token 校验所有 HTTP/WS 请求 |
| 2 | 未设 token，但 `DOCRESTORE_ALLOW_INSECURE=1` | **无鉴权逃生口**：仅供本机调试，且只允许绑定 loopback（见下方 bind 守卫），否则拒绝启动 |
| 3 | 默认（未设 token，未开 insecure） | **自动生成**强随机 device token，持久化到本地配置目录并打印；重启复用，配对的手机不失效 |

device token 持久化路径（跨平台）：

- Linux/macOS：`$XDG_CONFIG_HOME/docrestore/device_token`，未设 `XDG_CONFIG_HOME` 时退化 `~/.config/docrestore/device_token`
- Windows：`%APPDATA%\docrestore\device_token`
- 文件权限 POSIX 下 `0600`（仅 owner 读写）；落地失败则本次用内存临时 token（重启后需重新配对，会打 warning）

这个自动生成的 token 即将来**手机配对二维码**里的 pairing secret——`{服务地址, token}` 扫码即配对，传输层（mesh/中继）后续叠加，鉴权模型不变。

#### 请求携带 token

- HTTP：`Authorization: Bearer <token>`（标准），或 `?token=<token>` query（`<img src>` / `<a href>` 等无法设 Header 的场景）
- WebSocket：仅 `?token=<token>`（浏览器原生 WS API 不支持自定义 Header）
- 缺/错 token → `401 UNAUTHORIZED`（结构化错误体）

#### bind 守卫（防误开放）

`DOCRESTORE_ALLOW_INSECURE=1`（无鉴权）时，启动校验绑定地址 `DOCRESTORE_BIND_HOST`（由 `start.sh` 导出）：

- 环回（`127.0.0.1` / `::1` / `localhost`）→ 放行，打高危 warning
- 非环回（`0.0.0.0` / 局域网 IP 等）→ **拒绝启动**，提示「要对外暴露请配置 `DOCRESTORE_API_TOKEN`」
- `DOCRESTORE_BIND_HOST` 未设（如直接 `uvicorn` 起、无法判定）→ 放行但打 warning，提醒 insecure 仅应用于 loopback

有 token（模式 1 或 3）时，绑任意地址都安全（每个请求都校验），手机/局域网访问直接配 `BACKEND_HOST=0.0.0.0` 即可。

#### CORS

API 默认**不挂** CORS 中间件（最严格：浏览器跨源请求被拦）。主要客户端是手机 App / 同源 Web UI，通常无需 CORS。确有浏览器跨源场景时，用 `DOCRESTORE_CORS_ORIGINS`（逗号分隔的 origin allowlist）显式放行，默认空=不放行任何跨源。

#### 与安全审查 issue #35 的偏离说明

issue #35 原修复方案写的是「未配 token 时仅绑 loopback 或拒启」。因产品方向（桌面服务 + 手机配对需 LAN/远程可达），纯 loopback 会挡掉手机端，故改为**等价或更强**的「默认自动生成 token + 始终 fail-closed」：任何路径都不会出现「未鉴权且对外可达」，loopback-only 仅保留为 insecure 逃生口的约束。

### 3.6 请求级配置覆盖安全（防 RCE / SSRF）

创建任务时请求体可携带配置覆盖（`ocr` / `llm` 等），但**基础设施字段绝不接受请求级覆盖**（#32 / #33）：

- **OCR 基础设施字段**——解释器路径（`paddle_python` / `paddle_server_python`）、worker 脚本、推理服务地址（`paddle_server_url` / `paddle_server_host` / `paddle_server_port` / `model_path` 等）已从请求 schema 移除，合成生效配置时再用 **allowlist 默认拒绝**、只放行登记过的业务字段（`model` / `gpu_id` / `exclude_images` / `paddle_pipeline` / `paddle_ocr_timeout`）。这样即便将来 schema 误加同类基础设施字段也进不了生效配置。可控即任意二进制执行（RCE）或把页面图发往攻击者/内网（SSRF），故只由服务端配置注入。
- **LLM `api_base`**（中转站地址）保留请求级可填，但过 SSRF 守卫：
  - 仅允 `http` / `https`；
  - 解析 host 的全部 IP，落入**私网（RFC1918）/ 链路本地（含云元数据 `169.254.169.254`）/ 保留 / 多播 / 未指定** → 拒绝（`400 LLM_API_BASE_REJECTED`）；
  - **环回（`127.0.0.1` / `::1` / `localhost`）放行**——同机本地 LLM（ollama / vLLM）的合法目标；
  - 可选白名单逃生口（见下）。

| 环境变量 | 默认 | 作用 |
|----------|------|------|
| `DOCRESTORE_LLM_API_BASE_ALLOWLIST` | 空 | LLM `api_base` 的 host 白名单（逗号分隔）。**空**=不启用白名单，按上面 SSRF 规则放行公网+环回、挡私网/内网；**非空**=只许命中白名单的 host（命中即放行，含你信任的**内网中转站**——把 LAN 上的本地 LLM 接进来就靠这个） |

> ⚠️ **白名单与环回的交互**：一旦设了 `DOCRESTORE_LLM_API_BASE_ALLOWLIST`，白名单即「唯一真相」，**环回不再自动放行**——未命中一律拒。若你既用云/内网中转站、又跑同机本地 LLM，需把本地 LLM 的 host **字面量**也写进白名单（`localhost` 与 `127.0.0.1` / `::1` 是不同字面量，按你 `api_base` 实际填的那个加）。

**与 issue #33 的偏离说明**：issue 写「拦截私网 / **环回** / 链路本地」。但「本地 LLM」（provider=local）的合法 `api_base` 就是 `http://localhost:11434/v1` 这类环回地址，照搬会误杀该功能。故环回放行（单用户桌面下环回 SSRF 价值极低，高价值目标——云元数据、内网横向——仍拦），LAN 上的本地 LLM 走白名单逃生口。

**残留风险**：DNS rebinding（校验时解析到公网、litellm 实际发包时 TTL 过期重绑内网）未防——需 connect 级 IP pin，属过度工程，已记入 known-issues 暂不实现。

### 3.7 输出目录边界（防任意目录删除）

任务 `output_dir`（请求级可填，前端「输出目录」输入框）在**删除任务时**会被 `shutil.rmtree` 递归删除。若不设防，构造一个非法 `image_dir` 让任务快速进终态（FAILED），再 `DELETE /tasks/{id}`，即可让服务进程删除任意目录（数据丢失、`ignore_errors=True` 静默不可逆）（#34）。

守卫：`output_dir` 必须**严格落在受信工作根之下**（`resolve()` 折叠 `..` / 符号链接后判定，且不能等于工作根本身——等于根＝授权删整个根）：

- 建任务时越界 → `400 OUTPUT_DIR_REJECTED`，不建任务；
- 删除时 rmtree 前**再次**校验（TOCTOU 防御：覆盖建任务校验前就存在的历史越界任务、DB 篡改）——越界则**拒删、绝不触碰目录**，任务保留在列表里让你察觉异常。

| 环境变量 | 默认 | 作用 |
|----------|------|------|
| `DOCRESTORE_WORK_ROOT` | 空（=系统临时目录） | 受信工作根。**空**=系统临时目录（正是默认输出 `{tempdir}/docrestore_{id}` 的父目录）；**非空**=用它做工作根。**想把产物落到持久化目录**（如 `/data/docrestore`）、或让自定义 `output_dir` 指向非临时路径时，必须把该目录（或其祖先）设为工作根，否则越界被拒。 |

> ⚠️ 默认只允许 `output_dir` 落在系统临时目录下（重启 / `/tmp` 清理策略可能擦除）。要**持久化产物**，请设 `DOCRESTORE_WORK_ROOT` 指向持久目录，再让 `output_dir` 落在其下。

`image_dir` 不施加同约束：它是**只读输入**、从不被删除，且合法用法会指向 NAS / 外部只读目录——加边界反而误杀正常用法（详见 known-issues #34）。

## 4. OCR 引擎配置

### 4.1 引擎选择

通过 `OCRConfig.model` 指定，支持以下标识符：

| 标识符 | 引擎 | 说明 |
|--------|------|------|
| `paddle-ocr/ppocr-v4`（默认） | PaddleOCR | 轻量级，独立 conda 环境 |
| `paddle-ocr` | PaddleOCR | 简写形式 |
| `deepseek/ocr-2` | DeepSeek-OCR-2 | 高精度 grounding OCR |
| `deepseek` | DeepSeek-OCR-2 | 简写形式 |

**GPU 选择**：`OCRConfig.gpu_id`（默认 `None`）统一控制两个引擎使用的 GPU。未显式指定时，后端在启动 ppocr-server 前调用 `docrestore.ocr.gpu_detect.pick_best_gpu()` 自动挑选 GPU（优先 CUDA compute capability，其次空闲显存、总显存）；前端任务表单会调用 `GET /api/v1/gpus` 拉取列表并允许用户在下拉中切换；也可通过环境变量 `PPOCR_GPU_ID` 显式指定。

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
| `paddle_server_model_name` | server 模型名（默认 `PaddleOCR-VL-1.6-0.9B`） |
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

`LLMConfig` 字段（`backend/docrestore/pipeline/config.py`）：

| 字段 | 默认 | 说明 |
|------|------|------|
| `provider` | `"cloud"` | `"cloud"` 走 litellm + LLM PII 实体识别；`"local"` 走本地 OpenAI 兼容服务，跳过 LLM 实体识别只走 regex 脱敏 |
| `model` | — | litellm 模型名；本地服务建议带 `openai/` 前缀（OpenAI schema 兜底） |
| `api_base` | `""` | 自定义 API 地址；本地模式必填 |
| `api_key` | `""` | 留空时由 litellm 从 `.env` 自动读取；本地模式可留空 |
| `max_concurrent_requests` | `3` | 全局并发上限（跨 pipeline 共享 asyncio.Semaphore） |
| `code_refine_mode` | `"refine"` | 代码模式：`refine` 行数守恒；`rewrite` 允许重排（需更强模型） |

#### 云端示例

```yaml
llm:
  provider: "cloud"
  model: "openai/gemini-3-flash-preview-nothinking"
  api_base: "https://poloai.top/v1"
  api_key: ""                # 为空时从环境变量自动读取
```

#### 本地示例（ollama）

```yaml
llm:
  provider: "local"
  model: "openai/qwen2.5:14b"            # ollama 拉取的模型 tag
  api_base: "http://localhost:11434/v1"  # ollama OpenAI 兼容端点
  api_key: ""                            # 本地服务无需鉴权
```

#### 本地示例（vLLM）

```yaml
llm:
  provider: "local"
  model: "openai/Qwen/Qwen2.5-14B-Instruct"
  api_base: "http://localhost:8001/v1"
  api_key: ""
```

> 前端 UI 上的"Provider"radio 与本字段一一对应；REST API 在 `POST /api/v1/tasks` 请求体的 `llm` 字段里传，详见 [API 文档](backend/api.md) 和 README §REST API 示例。

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
ExecStart=/path/to/conda/envs/ppocr_vlm/bin/paddleocr genai_server --model_name PaddleOCR-VL-1.6-0.9B --backend vllm --port 8119
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
