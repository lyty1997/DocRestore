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

# API Layer (api/)

## 1. Responsibilities

The API layer exposes REST APIs and WebSocket endpoints:

- Create tasks (asynchronous execution)
- Query task status and progress
- Retrieve final results (markdown + output path)
- Download single-task result zip (`document.md` + `images/`)
- Restricted static asset access (whitelist + path traversal protection)

The API layer depends only on the Pipeline layer (scheduling/task management/result models) and does not directly depend on OCR/processing/LLM/output or other internal implementation details.

## 2. File List

| File | Responsibility |
|---|---|
| `api/app.py` | FastAPI application creation, lifecycle management, PipelineScheduler injection |
| `api/routes.py` | REST/WS route definitions |
| `api/schemas.py` | Pydantic request/response models |

## 3. Public Interface (/api/v1)

### 3.1 FastAPI Application (api/app.py)

```python
def create_app(config: PipelineConfig | None = None) -> FastAPI:
    """Create a FastAPI application.

    - lifespan/startup: Initialize Pipeline (may load OCR models)
    - shutdown: Release resources
    """
    app = FastAPI(title="DocRestore", version="0.1.0")
    app.include_router(router, prefix="/api/v1")
    return app
```

### 3.2 Data Structures (api/schemas.py)

The API layer uses pydantic `BaseModel` (single source: request-side `*Request` + response-side `*Response`). Key schemas are listed below grouped by topic; for complete fields, refer to `backend/docrestore/api/schemas.py`.

**Request-level configuration overrides** (three optional sub-objects on `CreateTaskRequest`; all fields are optional -- unset fields inherit server startup defaults):

```python
class LLMConfigRequest(BaseModel):
    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    max_chars_per_segment: int | None = None

class OCRConfigRequest(BaseModel):
    model: str | None = None               # e.g. "paddle-ocr/ppocr-v4" / "deepseek/ocr-2"
    gpu_id: str | None = None              # CUDA_VISIBLE_DEVICES
    paddle_python: str | None = None
    paddle_ocr_timeout: int | None = None
    paddle_server_url: str | None = None
    paddle_server_model_name: str | None = None

class CustomSensitiveWord(BaseModel):
    word: str
    code: str | None = None                # Empty means use custom_words_placeholder

class PIIConfigRequest(BaseModel):
    enable: bool | None = None
    # Accepts both list[str] (plain strings) and list[CustomSensitiveWord] (objects)
    custom_sensitive_words: list[CustomSensitiveWord] | list[str] | None = None
```

The route helper `_to_custom_words()` uniformly converts both forms into `list[CustomWord]` and writes them into `pii_override`.

**Task lifecycle**:

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

**Task results (multi-document compatible)**:

```python
class TaskResultResponse(BaseModel):
    task_id: str
    output_path: str
    markdown: str
    doc_title: str = ""                    # Multi-document identifier (empty for single document)
    doc_dir: str = ""                      # Subdirectory name relative to task.output_dir

class TaskResultsResponse(BaseModel):
    task_id: str
    results: list[TaskResultResponse]      # LLM boundary detection may produce >1 items
```

**Task list (persistent, paginated)**:

```python
class TaskListItem(BaseModel):
    task_id: str; status: str
    image_dir: str; output_dir: str
    error: str | None = None
    created_at: str
    result_count: int = 0                  # Number of sub-documents produced by this task

class TaskListResponse(BaseModel):
    tasks: list[TaskListItem]
    total: int; page: int; page_size: int  # page_size capped at 100
```

**Source selection (local upload / server files)**:

```python
class DirEntry(BaseModel):
    name: str
    is_dir: bool
    size_bytes: int | None = None          # Present when is_dir=False
    image_count: int | None = None         # Optionally present when is_dir=True

class BrowseDirsResponse(BaseModel):
    path: str
    parent: str | None = None
    entries: list[DirEntry]                # Includes files when include_files=True

class StageServerSourceRequest(BaseModel):
    paths: list[str]                       # Absolute paths, max 5000 items
class StageServerSourceResponse(BaseModel):
    image_dir: str                         # Composed of temp directory + symlinks
    file_count: int

class SourceImagesResponse(BaseModel):
    task_id: str
    images: list[str]
```

**Chunked upload session** (used by the frontend `FileUploader` flow):

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

### 3.3 Routes (api/routes.py + api/upload.py)

Endpoint overview:

| Method | Path | Description |
|---|---|---|
| `GET` | `/tasks?status=&page=&page_size=` | Paginated task list (`status` filter, `page_size` capped at 100) |
| `POST` | `/tasks` | Create a task and start it immediately |
| `GET` | `/tasks/{id}` | Query task status |
| `GET` | `/tasks/{id}/result` | Single-document result (returns first item for multi-document tasks) |
| `GET` | `/tasks/{id}/results` | Multi-document result list (`TaskResultsResponse`) |
| `PUT` | `/tasks/{id}/results/{result_index}` | Update a specific sub-document's Markdown (by index, for manual refinement) |
| `GET` | `/tasks/{id}/assets/{asset_path:path}` | Restricted asset access (whitelist + path traversal protection) |
| `GET` | `/tasks/{id}/download` | Download task result zip (includes multi-document subdirectories) |
| `GET` | `/tasks/{id}/source-images` | List task input image filenames |
| `GET` | `/tasks/{id}/source-images/{filename:path}` | Retrieve a task input source image file |
| `POST` | `/tasks/{id}/cancel` | Cancel a running task |
| `POST` | `/tasks/{id}/retry` | Retry a failed task (creates a new task) |
| `DELETE` | `/tasks/{id}` | Delete task record and artifacts |
| `WS` | `/tasks/{id}/progress` | WebSocket progress push (protected by `require_auth_ws`) |
| `GET` | `/filesystem/dirs?path=&include_files=` | Server directory browsing |
| `POST` | `/sources/server` | Stage server files as `image_dir` (symlinks, max 5000) |
| `POST` | `/uploads` | Create an upload session |
| `POST` | `/uploads/{sid}/files` | Upload a batch of files (supports `paths` to preserve relative paths) |
| `GET` | `/uploads/{sid}/files` | List files in a session |
| `GET` | `/uploads/{sid}/files/{fid}` | Preview a single file in a session |
| `DELETE` | `/uploads/{sid}/files/{fid}` | Delete a single file in a session |
| `POST` | `/uploads/{sid}/complete` | Complete the session, returns a temp directory usable as `image_dir` |

#### POST /api/v1/tasks -- Create Task

- Behavior: Creates a task record -> `asyncio.create_task(run_task(...))` runs asynchronously -> immediately returns `TaskResponse`
- Note: The `llm` / `ocr` / `pii` fields are used for "request-level overrides" of the default configuration (omitting them uses the server startup `PipelineConfig`). The route synthesizes the final `LLMConfig` / `OCRConfig` / `PIIConfig` complete snapshots and passes them to `TaskManager.create_task`; the downstream Pipeline consumes them directly.

Request example:

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
      {"word": "John Doe", "code": "Alias-A"},
      "internal codename"
    ]
  }
}
```

Response example:

```json
{
  "task_id": "a1b2c3d4",
  "status": "pending",
  "progress": null,
  "error": null
}
```

#### GET /api/v1/tasks/{task_id} -- Query Task Status

Response example (processing):

```json
{
  "task_id": "a1b2c3d4",
  "status": "processing",
  "progress": {
    "stage": "ocr",
    "current": 5,
    "total": 26,
    "percent": 19.2,
    "message": "Running OCR on photo 5..."
  },
  "error": null
}
```

Response example (completed):

```json
{
  "task_id": "a1b2c3d4",
  "status": "completed",
  "progress": null,
  "error": null
}
```

#### GET /api/v1/tasks/{task_id}/result -- Single-document Result (Backward Compatible)

- Only returns `TaskResultResponse` when the task is `completed` and `task.result` is non-empty
- For multi-document tasks, returns the first item; use `/results` for the complete list
- Returns HTTP 404 if the task is incomplete or does not exist

Response example:

```json
{
  "task_id": "a1b2c3d4",
  "output_path": "/path/to/output/doc-1/document.md",
  "markdown": "# Title\n\n...",
  "doc_title": "Work Report",
  "doc_dir": "doc-1"
}
```

#### GET /api/v1/tasks/{task_id}/results -- Multi-document Result List

Returns `TaskResultsResponse` where each item in `results` corresponds to a sub-document (single-document tasks have length 1; `doc_title` / `doc_dir` may be empty).

#### PUT /api/v1/tasks/{task_id}/results/{result_index} -- Manual Refinement Save

- `result_index` is the index (0-based) into `task.results`
- Request body: `UpdateMarkdownRequest { markdown: str }`; `TaskManager.update_result_markdown()` writes back to `output_path` and synchronizes the in-memory state
- Failure scenarios return 400 (e.g. index out of bounds, task not completed, file write failure)

#### WS /api/v1/tasks/{task_id}/progress -- Real-time Progress Push

- WebSocket connection; the sender pushes initial progress first, then incrementally pushes progress events, and closes the connection when the task terminates
- Authentication is verified during handshake via `require_auth_ws`; unauthenticated or non-existent tasks receive close codes 1008/1011
- Payload matches `TaskProgress` dataclass fields (`stage/current/total/percent/message`)
- See [ws_progress.md](ws_progress.md) for details

#### GET /api/v1/tasks/{task_id}/assets/{asset_path:path} -- Restricted Asset Access

- Whitelist: `document.md` / `images/**` / `{doc_dir}/document.md` / `{doc_dir}/images/**`
- Security policy (`_validate_asset_path` + `_resolve_asset_path`):
  - Rejects absolute paths, `..`, `.`, and other illegal segments
  - `Path.resolve()` + `is_relative_to()` prevents symlink/path traversal attacks
- Used for frontend preview/incremental resource loading. See [result_delivery.md](result_delivery.md) for details

#### GET /api/v1/tasks/{task_id}/download -- Download Result Zip

- Packages based on `task.results`' `doc_dir` list:
  - Single document (`doc_dir` is empty): `document.md` + `images/`
  - Multiple documents: each sub-document occupies a subdirectory layer
- Download is allowed only when at least one `document.md` exists; otherwise returns 404

Zip structure example (multi-document):

```
doc-1/document.md
doc-1/images/...
doc-2/document.md
doc-2/images/...
```

#### GET /api/v1/tasks/{task_id}/source-images[/{filename:path}] -- Source Image Access

- `/source-images`: Returns `SourceImagesResponse { task_id, images: list[str] }` where `images` contains POSIX paths relative to `image_dir`, sorted by name (`rglob` scan runs in a thread pool)
- `/source-images/{filename:path}`: Returns a single image; rejects absolute paths and `..`, resolves and verifies `is_relative_to(image_dir)`, and requires the suffix to be in `_IMAGE_EXTS` (`.jpg/.jpeg/.png/.bmp/.tiff/.tif`)

#### POST /api/v1/tasks/{task_id}/cancel | /retry & DELETE /api/v1/tasks/{task_id}

- `cancel`: Cancels a running task; returns 409 for non-running states
- `retry`: Copies a failed task's `image_dir / output_dir / config snapshot` into a new task and starts it; the response `task_id` is the new task's ID
- `DELETE`: Deletes the task record and artifact directory; returns 409 if the task is still running

#### GET /api/v1/filesystem/dirs -- Server Directory Browsing

- `path` supports `~` expansion, defaults to `~`
- When `include_files=True`: directory entries include `image_count` (top-level image count preview, capped at 9999), file entries include `size_bytes`, and only files with extensions in `_IMAGE_EXTS` are listed
- Unreadable directories/files are silently skipped; hidden items (starting with `.`) are not listed

#### POST /api/v1/sources/server -- Stage Server Files as image_dir

- Request: `StageServerSourceRequest { paths: list[str] }` where each path must be absolute, exist, be a regular file, and have an extension in `_IMAGE_EXTS`
- Count limit: `_STAGE_FILES_MAX=5000`
- The backend creates symlinks for each source file in `tempfile.mkdtemp(prefix="docrestore_src_")`; name conflicts are resolved by appending `_1/_2/...`
- Returns the temp directory path (callers manage it after use; no automatic cleanup)

#### Upload Session Chain (/api/v1/uploads/...)

The frontend `FileUploader` component uses the following flow:

| Step | Method | Path | Description |
|---|---|---|---|
| Create session | `POST` | `/uploads` | Returns `session_id`, `max_file_size_mb=50`, allowed extensions |
| Upload | `POST` | `/uploads/{sid}/files` | Multipart; optional `paths` field preserves relative paths |
| List | `GET` | `/uploads/{sid}/files` | `UploadFileItem[]`, sorted by `relative_path` |
| Preview | `GET` | `/uploads/{sid}/files/{fid}` | Image binary (with path traversal protection) |
| Delete file | `DELETE` | `/uploads/{sid}/files/{fid}` | Deletes and cleans up empty parent directories; returns 400 if session is completed |
| Complete | `POST` | `/uploads/{sid}/complete` | Marks session as complete, returns `image_dir` (temp directory) that can be fed directly to `create_task` |

- Session TTL: `_SESSION_TTL_SECONDS=3600`; background cleanup runs every `_CLEANUP_INTERVAL_SECONDS=1800`
- On app shutdown, `cleanup_all_sessions()` deletes all temporary directories as a safety net

## 4. Dependencies

| Source | Usage |
|---|---|
| `pipeline/task_manager.py` | `TaskManager`, `Task`, `TaskStatus` |
| `pipeline/scheduler.py` | `PipelineScheduler` (injected/initialized in app.py/lifespan) |
| `pipeline/config.py` | `PipelineConfig` |
| `models.py` | `TaskProgress`, `PipelineResult` |

The API layer does not directly depend on the OCR layer, processing layer, LLM layer, or output layer.

## 5. Internal Implementation Notes

- **Request-level config synthesis** (`create_task`): The route is the sole synthesis point for "incremental fields -> complete Config". For each Config type, it takes the default from `manager.pipeline.config`, overlays non-null fields via `model_copy(update=req.xxx.model_dump(exclude_none=True))`, and converts PII custom sensitive words through `_to_custom_words()` into `CustomWord`. Downstream `TaskManager` / Pipeline performs no further merging.
- **Background execution**: `asyncio.create_task(manager.run_task(...))` + `manager.register_running_task()`; on cancel/delete, TaskManager interrupts and cleans up the handle.
- **WebSocket broadcast**: `TaskManager.subscribe_progress()` returns a single-producer multi-consumer `asyncio.Queue` (maxsize=1 for back-pressure); `unsubscribe_progress()` is called on connection close.
- **Asset security**: `_validate_asset_path` + `_resolve_asset_path` dual-layer verification (whitelist + `is_relative_to` traversal prevention), allowing subdirectory patterns for multi-document structure compatibility.
- **Zip assembly**: `_build_result_zip_bytes` assembles in memory using `ZIP_DEFLATED`; empty `doc_dir` goes to the root directory, non-empty uses a subdirectory prefix.

## 6. Error Responses (MVP)

During the MVP phase, fairly complete error information is returned for development debugging:

- When a task fails, `TaskResponse.error` may contain a full traceback
- Query endpoints (GET `/api/v1/tasks/{task_id}`) return `error` when in `failed` state

Example:

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

Before going to production, this should be tightened to structured errors (error codes + user-friendly messages) to avoid exposing internal stack traces.

## 7. Future Iterations

- MCP Server (AI agent integration, e.g. Claude Desktop / RAG agent autonomous invocation)
- Structured error responses (error codes + user-friendly messages, tightening traceback exposure)
