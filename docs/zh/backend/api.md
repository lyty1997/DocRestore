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

# API 层（api/）

## 1. 职责

API 层对外暴露 REST API 与 WebSocket：

- 创建任务（异步执行）
- 查询任务状态与进度
- 获取最终结果（markdown + 输出路径）
- 下载单任务结果 zip（`document.md` + `images/`）
- 受限静态资源访问（白名单 + 路径穿越防护）

API 层只依赖 Pipeline 层（调度/任务管理/结果模型），不直接依赖 OCR/处理/LLM/输出等内部实现细节。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `api/app.py` | FastAPI 应用创建、生命周期管理、PipelineScheduler 注入 |
| `api/routes.py` | REST/WS 路由定义 |
| `api/schemas.py` | Pydantic 请求/响应模型 |

## 3. 对外接口（/api/v1）

### 3.1 FastAPI 应用（api/app.py）

```python
def create_app(config: PipelineConfig | None = None) -> FastAPI:
    """创建 FastAPI 应用。

    - lifespan/startup：初始化 Pipeline（可能加载 OCR 模型）
    - shutdown：释放资源
    """
    app = FastAPI(title="DocRestore", version="0.1.0")
    app.include_router(router, prefix="/api/v1")
    return app
```

### 3.2 数据结构（api/schemas.py）

API 层使用 pydantic `BaseModel`（同源：请求侧 `*Request` + 响应侧 `*Response`）。以下按主题分组列出关键 schema；完整字段以 `backend/docrestore/api/schemas.py` 为准。

**请求级配置覆盖**（`CreateTaskRequest` 的三个可选子对象，所有字段均可选，未填写则继承服务启动默认值）：

```python
class LLMConfigRequest(BaseModel):
    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    max_chars_per_segment: int | None = None

class OCRConfigRequest(BaseModel):
    model: str | None = None               # 如 "paddle-ocr/ppocr-v4" / "deepseek/ocr-2"
    gpu_id: str | None = None              # CUDA_VISIBLE_DEVICES
    paddle_python: str | None = None
    paddle_ocr_timeout: int | None = None
    paddle_server_url: str | None = None
    paddle_server_model_name: str | None = None

class CustomSensitiveWord(BaseModel):
    word: str
    code: str | None = None                # 为空则使用 custom_words_placeholder

class PIIConfigRequest(BaseModel):
    enable: bool | None = None
    # 兼容 list[str]（纯字符串）和 list[CustomSensitiveWord]（对象）两种写法
    custom_sensitive_words: list[CustomSensitiveWord] | list[str] | None = None
```

路由 `_to_custom_words()` 统一将两种形态转为 `list[CustomWord]`，写入 `pii_override`。

**任务生命周期**：

```python
class CreateTaskRequest(BaseModel):
    image_dir: str
    output_dir: str | None = None
    llm: LLMConfigRequest | None = None
    ocr: OCRConfigRequest | None = None
    pii: PIIConfigRequest | None = None

class ProgressResponse(BaseModel):
    stage: str; current: int; total: int; percent: float; message: str

class TaskResponse(BaseModel):
    task_id: str
    status: str                            # pending / processing / completed / failed
    progress: ProgressResponse | None = None
    error: str | None = None

class ActionResponse(BaseModel):
    task_id: str; message: str = ""

class UpdateMarkdownRequest(BaseModel):
    markdown: str
```

**任务结果（多文档兼容）**：

```python
class TaskResultResponse(BaseModel):
    task_id: str
    output_path: str
    markdown: str
    doc_title: str = ""                    # 多文档标识（单文档为空）
    doc_dir: str = ""                      # 相对 task.output_dir 的子目录名

class TaskResultsResponse(BaseModel):
    task_id: str
    results: list[TaskResultResponse]      # LLM 文档边界检测可能产生 >1 项
```

**任务列表（持久化，分页）**：

```python
class TaskListItem(BaseModel):
    task_id: str; status: str
    image_dir: str; output_dir: str
    error: str | None = None
    created_at: str
    result_count: int = 0                  # 该任务产出的子文档数量

class TaskListResponse(BaseModel):
    tasks: list[TaskListItem]
    total: int; page: int; page_size: int  # page_size 上限 100
```

**来源选择（本地上传 / 服务器文件）**：

```python
class DirEntry(BaseModel):
    name: str
    is_dir: bool
    size_bytes: int | None = None          # is_dir=False 时携带
    image_count: int | None = None         # is_dir=True 时可选携带

class BrowseDirsResponse(BaseModel):
    path: str
    parent: str | None = None
    entries: list[DirEntry]                # 当 include_files=True 时包含文件

class StageServerSourceRequest(BaseModel):
    paths: list[str]                       # 绝对路径，上限 5000 项
class StageServerSourceResponse(BaseModel):
    image_dir: str                         # 由临时目录 + 符号链接组成
    file_count: int

class SourceImagesResponse(BaseModel):
    task_id: str
    images: list[str]
```

**分片上传会话**（前端 `FileUploader` 流程使用）：

```python
class UploadSessionResponse(BaseModel):
    session_id: str
    max_file_size_mb: int
    allowed_extensions: list[str]

class UploadFilesResponse(BaseModel):
    session_id: str
    uploaded: list[str]
    total_uploaded: int
    failed: list[str]

class UploadFileItem(BaseModel):
    session_id: str; file_id: str
    filename: str; relative_path: str
    size_bytes: int; created_at: str

class UploadSessionFilesResponse(BaseModel):
    session_id: str
    files: list[UploadFileItem]

class UploadSessionFileDeleteResponse(BaseModel):
    session_id: str; file_id: str
    remaining_count: int

class UploadCompleteResponse(BaseModel):
    session_id: str
    image_dir: str
    file_count: int
    total_size_bytes: int
```

**OCR 引擎按需预热**（前端 TaskForm "预加载引擎" 按钮使用）：

```python
class OCRWarmupRequest(BaseModel):
    model: str = "paddle-ocr/ppocr-v4"
    gpu_id: str = "1"

class OCRStatusResponse(BaseModel):
    current_model: str
    current_gpu: str
    is_ready: bool       # _engine.is_ready
    is_switching: bool   # _switch_lock.locked()
```

### 3.3 路由（api/routes.py + api/upload.py）

端点总览：

| 方法 | 路径 | 描述 |
|---|---|---|
| `GET` | `/tasks?status=&page=&page_size=` | 分页列出任务（`status` 过滤，`page_size` 上限 100） |
| `POST` | `/tasks` | 创建任务并立即启动 |
| `GET` | `/tasks/{id}` | 查询任务状态 |
| `GET` | `/tasks/{id}/result` | 单文档结果（多文档时返回首项） |
| `GET` | `/tasks/{id}/results` | 多文档结果列表（`TaskResultsResponse`） |
| `PUT` | `/tasks/{id}/results/{result_index}` | 更新指定子文档 Markdown（按索引定位，人工精修） |
| `GET` | `/tasks/{id}/assets/{asset_path:path}` | 受限资源访问（白名单 + 路径穿越防护） |
| `GET` | `/tasks/{id}/download` | 下载任务结果 zip（含多文档子目录） |
| `GET` | `/tasks/{id}/source-images` | 列出任务输入图片文件名 |
| `GET` | `/tasks/{id}/source-images/{filename:path}` | 获取任务输入源图片文件 |
| `POST` | `/tasks/{id}/cancel` | 取消运行中的任务 |
| `POST` | `/tasks/{id}/retry` | 重试失败的任务（创建新任务） |
| `DELETE` | `/tasks/{id}` | 删除任务及产物 |
| `WS` | `/tasks/{id}/progress` | WebSocket 进度推送（受 `require_auth_ws` 保护） |
| `GET` | `/filesystem/dirs?path=&include_files=` | 服务器目录浏览 |
| `POST` | `/sources/server` | 将服务器文件 stage 为 `image_dir`（符号链接，上限 5000） |
| `POST` | `/uploads` | 创建上传会话 |
| `POST` | `/uploads/{sid}/files` | 上传一批文件（支持 `paths` 保留相对路径） |
| `GET` | `/uploads/{sid}/files` | 列出会话内文件 |
| `GET` | `/uploads/{sid}/files/{fid}` | 预览会话内单个文件 |
| `DELETE` | `/uploads/{sid}/files/{fid}` | 删除会话内单个文件 |
| `POST` | `/uploads/{sid}/complete` | 完成会话，返回可用于 `image_dir` 的临时目录 |

#### POST /api/v1/tasks — 创建任务

- 行为：创建任务记录 → `asyncio.create_task(run_task(...))` 异步运行 → 立即返回 `TaskResponse`
- 备注：`llm` / `ocr` / `pii` 字段用于"请求级覆盖"默认配置（不传则使用服务启动时的默认 `PipelineConfig`）。路由合成最终 `LLMConfig` / `OCRConfig` / `PIIConfig` 完整快照后传入 `TaskManager.create_task`，下游 Pipeline 直接消费。

请求示例：

```json
{
  "image_dir": "/path/to/images",
  "output_dir": "/path/to/output",
  "llm": {
    "model": "openai/glm-5",
    "api_key": "<YOUR_API_KEY>"
  },
  "ocr": { "model": "paddle-ocr/ppocr-v4", "gpu_id": "1" },
  "pii": {
    "enable": true,
    "custom_sensitive_words": [
      {"word": "张伟", "code": "化名A"},
      "公司内部代号"
    ]
  }
}
```

响应示例：

```json
{
  "task_id": "a1b2c3d4",
  "status": "pending",
  "progress": null,
  "error": null
}
```

#### GET /api/v1/tasks/{task_id} — 查询任务状态

响应示例（处理中）：

```json
{
  "task_id": "a1b2c3d4",
  "status": "processing",
  "progress": {
    "stage": "ocr",
    "current": 5,
    "total": 26,
    "percent": 19.2,
    "message": "正在 OCR 第 5 张照片..."
  },
  "error": null
}
```

响应示例（完成）：

```json
{
  "task_id": "a1b2c3d4",
  "status": "completed",
  "progress": null,
  "error": null
}
```

#### GET /api/v1/tasks/{task_id}/result — 单文档结果（兼容）

- 仅当任务 `completed` 且 `task.result` 非空时返回 `TaskResultResponse`
- 多文档任务返回首项；完整列表使用 `/results`
- 任务未完成/不存在：HTTP 404

响应示例：

```json
{
  "task_id": "a1b2c3d4",
  "output_path": "/path/to/output/doc-1/document.md",
  "markdown": "# 标题\n\n...",
  "doc_title": "工作报告",
  "doc_dir": "doc-1"
}
```

#### GET /api/v1/tasks/{task_id}/results — 多文档结果列表

返回 `TaskResultsResponse`，`results` 中每项对应一个子文档（单文档任务长度为 1，`doc_title` / `doc_dir` 可能为空）。

#### PUT /api/v1/tasks/{task_id}/results/{result_index} — 人工精修保存

- `result_index` 是 `task.results` 中的下标（0 起）
- 请求体 `UpdateMarkdownRequest { markdown: str }`，由 `TaskManager.update_result_markdown()` 回写到 `output_path` 并同步内存态
- 失败场景返回 400（如下标越界、任务未完成、文件写入失败）

#### WS /api/v1/tasks/{task_id}/progress — 实时进度推送

- WebSocket 连接，发送端先推送初始进度，随后增量推送进度事件，任务终止时关闭连接
- 握手时通过 `require_auth_ws` 校验；未认证或任务不存在返回 1008/1011 关闭码
- payload 与 `TaskProgress` dataclass 字段一致（`stage/current/total/percent/message`）
- 详见 [ws_progress.md](ws_progress.md)

#### GET /api/v1/tasks/{task_id}/assets/{asset_path:path} — 受限资源访问

- 白名单：`document.md` / `images/**` / `{doc_dir}/document.md` / `{doc_dir}/images/**`
- 安全策略（`_validate_asset_path` + `_resolve_asset_path`）：
  - 拒绝绝对路径、`..`、`.` 等非法片段
  - `Path.resolve()` + `is_relative_to()` 防止软链接/路径穿越
- 用于前端预览/增量加载资源。详见 [result_delivery.md](result_delivery.md)

#### GET /api/v1/tasks/{task_id}/download — 下载结果 zip

- 根据 `task.results` 的 `doc_dir` 列表打包：
  - 单文档（`doc_dir` 为空）：`document.md` + `images/`
  - 多文档：每个子文档占一层子目录
- 至少一个 `document.md` 存在时才允许下载，否则 404

zip 结构示例（多文档）：

```
doc-1/document.md
doc-1/images/...
doc-2/document.md
doc-2/images/...
```

#### GET /api/v1/tasks/{task_id}/source-images[/{filename:path}] — 源图片访问

- `/source-images`：返回 `SourceImagesResponse { task_id, images: list[str] }`，`images` 为相对 `image_dir` 的 POSIX 路径，按名称排序（`rglob` 扫描在线程池中执行）
- `/source-images/{filename:path}`：返回单张图片；拒绝绝对路径与 `..`，解析后 `is_relative_to(image_dir)` 校验，后缀必须在 `_IMAGE_EXTS`（`.jpg/.jpeg/.png/.bmp/.tiff/.tif`）内

#### POST /api/v1/tasks/{task_id}/cancel | /retry & DELETE /api/v1/tasks/{task_id}

- `cancel`：取消运行中的任务；非运行态返回 409
- `retry`：将失败任务的 `image_dir / output_dir / config snapshot` 复制为新任务并启动，响应 `task_id` 为新任务 ID
- `DELETE`：删除任务记录与产物目录；任务仍在运行时返回 409

#### GET /api/v1/filesystem/dirs — 服务器目录浏览

- `path` 支持 `~` 展开，默认 `~`
- `include_files=True` 时：目录条目附带 `image_count`（顶层图片数预览，封顶 9999），文件条目附带 `size_bytes`，仅列出 `_IMAGE_EXTS` 内的文件
- 不可读目录/文件静默跳过；隐藏项（以 `.` 开头）不列出

#### POST /api/v1/sources/server — 服务器文件聚合为 image_dir

- 请求：`StageServerSourceRequest { paths: list[str] }`，每条必须是绝对路径、存在、普通文件、后缀在 `_IMAGE_EXTS`
- 数量上限 `_STAGE_FILES_MAX=5000`
- 后端在 `tempfile.mkdtemp(prefix="docrestore_src_")` 中为每个源文件创建符号链接，名称冲突时追加 `_1/_2/...`
- 返回临时目录路径（调用方使用后自行管理，不自动清理）

#### 上传会话链（/api/v1/uploads/...）

前端 `FileUploader` 组件使用以下流程：

| 步骤 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 建会话 | `POST` | `/uploads` | 返回 `session_id`、`max_file_size_mb=50`、允许扩展名 |
| 上传 | `POST` | `/uploads/{sid}/files` | multipart，可选 `paths` 字段保留相对路径 |
| 列表 | `GET` | `/uploads/{sid}/files` | `UploadFileItem[]`，按 `relative_path` 排序 |
| 预览 | `GET` | `/uploads/{sid}/files/{fid}` | 图片二进制（路径穿越防护） |
| 删文件 | `DELETE` | `/uploads/{sid}/files/{fid}` | 删除并清理空父目录；会话已完成时 400 |
| 完成 | `POST` | `/uploads/{sid}/complete` | 标记会话完成，返回 `image_dir`（临时目录）可直接喂给 `create_task` |

- 会话 TTL `_SESSION_TTL_SECONDS=3600`，后台 `_CLEANUP_INTERVAL_SECONDS=1800` 轮询清理
- app shutdown 时 `cleanup_all_sessions()` 兜底删除所有临时目录

#### GET /api/v1/ocr/status — 查询当前 OCR 引擎状态

- 返回 `OCRStatusResponse { current_model, current_gpu, is_ready, is_switching }`，字段直接来自 `EngineManager` 同名属性
- `app.state.engine_manager` 未挂载时返回 500（注入引擎或未启动 lifespan 的测试场景）

#### POST /api/v1/ocr/warmup — 触发引擎预热

- 请求 `OCRWarmupRequest { model, gpu_id }`，缺省值 `paddle-ocr/ppocr-v4 + GPU 1`
- 响应 `{ status, message }`：
  - `ready`：当前引擎已就绪且 model/gpu_id 都匹配，立即返回
  - `switching`：`EngineManager._switch_lock` 被持有（其它请求触发的切换正在进行中）
  - `accepted`：用 `manager.pipeline.config.ocr.model_copy(update={...})` 合成完整 `OCRConfig`，`asyncio.create_task` 后台调用 `engine_manager.ensure(config)`，立即返回
- 后台预热失败仅 `logger.warning`，不会写回 API 响应；前端通过轮询 `/ocr/status` 自行判断终态

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `pipeline/task_manager.py` | `TaskManager`, `Task`, `TaskStatus` |
| `pipeline/scheduler.py` | `PipelineScheduler`（在 app.py/lifespan 中注入/初始化） |
| `pipeline/config.py` | `PipelineConfig` |
| `models.py` | `TaskProgress`, `PipelineResult` |

API 层不直接依赖 OCR 层、处理层、LLM 层或输出层。

## 5. 内部实现要点

- **请求级配置合成**（`create_task`）：路由是"增量字段 → 完整 Config"的唯一合成点。对每类 Config 取 `manager.pipeline.config` 默认值，用 `model_copy(update=req.xxx.model_dump(exclude_none=True))` 叠加非空字段，PII 自定义敏感词经 `_to_custom_words()` 统一转 `CustomWord`。下游 `TaskManager` / Pipeline 不再做合并。
- **后台执行**：`asyncio.create_task(manager.run_task(...))` + `manager.register_running_task()`；取消/删除时 TaskManager 负责中断并清理 handle。
- **WebSocket 广播**：`TaskManager.subscribe_progress()` 返回单生产者多消费者的 `asyncio.Queue`（maxsize=1 背压），连接断开时 `unsubscribe_progress()`。
- **Assets 安全**：`_validate_asset_path` + `_resolve_asset_path` 双层校验（白名单 + `is_relative_to` 防穿越），允许子目录形式以兼容多文档结构。
- **Zip 组装**：`_build_result_zip_bytes` 在内存中用 `ZIP_DEFLATED` 组装；空 `doc_dir` 走根目录，非空走子目录前缀。
- **按需引擎预热**：`lifespan` 中创建完 `EngineManager` 后，用 `asyncio.create_task` 后台调用 `engine_manager.ensure()` 预热默认引擎（不阻塞服务可用性，失败仅记 warning）；`/ocr/warmup` 也走同一条路径，但允许调用方覆盖 `model/gpu_id`。`EngineManager` 暴露 `current_gpu / is_ready / is_switching` 三个只读属性供 `/ocr/status` 直接映射。

## 6. 错误响应（MVP）

MVP 阶段返回较完整错误信息，便于开发调试：

- 任务失败时，`TaskResponse.error` 可能包含完整 traceback
- 查询接口（GET `/api/v1/tasks/{task_id}`）在 `failed` 状态下返回 `error`

示例：

```json
{
  "task_id": "a1b2c3d4",
  "status": "failed",
  "progress": {
    "stage": "ocr",
    "current": 3,
    "total": 26,
    "percent": 11.5,
    "message": "..."
  },
  "error": "Traceback (most recent call last):\n  ..."
}
```

上线前应收紧为结构化错误（错误码 + 用户友好消息），避免暴露内部堆栈。

## 7. 后续迭代

- MCP Server（AI agent 集成，如 Claude Desktop / RAG agent 自主调用）
- 结构化错误响应（错误码 + 用户友好消息，收紧 traceback 暴露）
