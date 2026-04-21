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

# OCR Layer (ocr/)

## 1. Responsibilities

Convert document photos into markdown text with grounding tags, while cropping illustration regions. The model stays resident on the GPU and supports continuous processing of multiple photos.

## 2. File List

| File | Responsibility |
|---|---|
| `ocr/base.py` | `OCREngine` Protocol + `WorkerBackedOCREngine` shared base class + contract filename constants |
| `ocr/engine_manager.py` | **Engine lifecycle manager** (on-demand switching, automatic ppocr-server management) |
| `ocr/router.py` | OCR engine factory (creates engines by model identifier) |
| `ocr/deepseek_ocr2.py` | DeepSeek-OCR-2 **subprocess client** (JSON Lines IPC) |
| `ocr/paddle_ocr.py` | PaddleOCR subprocess client (JSON Lines IPC) |
| `ocr/column_filter.py` | Sidebar detection and filtering (grounding coordinate analysis) |
| `ocr/preprocessor.py` | Image preprocessing (dynamic resolution + tile splitting, used only inside the worker) |
| `ocr/ngram_filter.py` | NoRepeatNGram loop suppression (used only inside the worker) |
| `scripts/deepseek_ocr_worker.py` | DeepSeek-OCR-2 worker process (vLLM inference, separate conda environment) |
| `scripts/paddle_ocr_worker.py` | PaddleOCR worker process (layout analysis + server calls, separate conda environment) |

## 3. Public Interface

### 3.1 OCREngine Protocol (ocr/base.py)

Other modules (Pipeline) call the OCR layer through this interface.

```python
ProgressFn = Callable[[str], None]  # 分阶段进度消息回调

class OCREngine(Protocol):
    async def initialize(self, on_progress: ProgressFn | None = None) -> None:
        """加载模型到 GPU。on_progress 推送长耗时初始化的分阶段消息"""
        ...

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        """单张 OCR，结果写入 output_dir/{image_stem}_OCR/，返回 PageOCR"""
        ...

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        """逐张调用 ocr()，每完成一张回调 on_progress(current, total)"""
        ...

    async def shutdown(self) -> None:
        """释放 GPU 资源"""
        ...

    @property
    def is_ready(self) -> bool: ...
```

**Contract filename constants** (`ocr/base.py`, shared with worker scripts):

```python
OCR_RESULT_FILENAME = "result.mmd"           # grounding 已解析、图片已裁剪替换的 markdown
OCR_RAW_RESULT_FILENAME = "result_ori.mmd"   # 原始输出（含 grounding 标签）
OCR_DEBUG_COORDS_FILENAME = "debug_coords.jsonl"
```

> Worker scripts (`scripts/*_ocr_worker.py`) run in separate conda environments and cannot import backend modules, so the same literal values must be **manually kept in sync** with these constants.

**Calling conventions**:
- `initialize()` must be called before `ocr()` / `ocr_batch()`
- Do not confuse the two callback types in `initialize(on_progress)`: `ProgressFn = Callable[[str], None]` (text messages during model loading), vs. the `on_progress` in `ocr_batch` which is `Callable[[int, int], None]` (per-image progress)
- `output_dir` is passed in by Pipeline; `ocr()` creates an `{image_stem}_OCR/` subdirectory under it
- The `PageOCR.raw_text` returned by `ocr()` contains grounding tags; `cleaned_text` is empty
- `ocr()` internally completes grounding parsing + image cropping, writing results to `PageOCR.output_dir`
- `ocr_batch()` calls images one by one -- no batch inference (intermediate results are needed for rolling merge)

### 3.2 WorkerBackedOCREngine (ocr/base.py)

Both the DeepSeek and Paddle engines are implemented via subprocess workers in separate conda environments. `WorkerBackedOCREngine(ABC)` extracts the shared skeleton:

- Worker script location (`_find_worker_script` supports absolute paths + fallback to repository-relative paths)
- Subprocess startup (uses `OCRConfig.worker_stdio_buffer_bytes` as the stdio buffer limit, default 16 MB, to avoid `LimitOverrunError` from large-image grounding JSON)
- JSON Lines command round-trips (`_send_command`) and protocol desync recovery (`_desync` flag)
- Default implementations of `ocr_batch` / `shutdown` / `_restart_worker`

Subclasses must implement: `engine_name` / `worker_script_path` class attributes, `_get_python_path` / `_get_timeout` / `_build_subprocess_env` / `_build_init_cmd` / `_terminate_process` / `ocr`.

## 4. Dependencies

| Source | Usage |
|---|---|
| `models.py` | `PageOCR`, `Region` |
| `pipeline/config.py` | `OCRConfig` |

Does not depend on any other processing layer modules.

## 5. Internal Implementation

### 5.1 EngineManager (ocr/engine_manager.py)

The engine lifecycle manager -- the core component. Switches OCR engines on demand (PaddleOCR <-> DeepSeek-OCR-2); only one engine occupies the GPU at any time.

```python
class EngineManager:
    def __init__(
        self,
        default_config: OCRConfig,
        gpu_lock: asyncio.Lock,
    ) -> None: ...

    async def ensure(
        self,
        ocr: OCRConfig | None = None,
    ) -> OCREngine:
        """确保引擎匹配请求的模型，必要时切换。

        `ocr` 为 None 时使用 `default_config`，否则视为本次请求的
        完整 OCRConfig 快照（由上游合成，EngineManager 内部不再合并）。

        切换时序：
        1. switch_lock 防止并发切换
        2. gpu_lock 等待当前 OCR 操作完成
        3. shutdown 旧引擎 + ppocr-server
        4. 启动 ppocr-server（如 PaddleOCR）
        5. 创建 + initialize 新引擎
        """

    async def shutdown(self) -> None:
        """应用关闭时：shutdown 引擎 + ppocr-server"""
```

**Automatic ppocr-server management**:
- PaddleOCR requires a running ppocr genai_server. EngineManager automatically starts the server as a subprocess when switching to PaddleOCR, and shuts it down when switching away.
- After startup, health is checked via TCP port polling: probe timeout `paddle_server_connect_timeout` (default `2.0s`), polling interval `paddle_server_poll_interval` (default `2.0s`); overall timeout `paddle_server_startup_timeout`.
- Shutdown strategy: SIGTERM -> wait(`paddle_server_shutdown_timeout`, default `10.0s`) -> SIGKILL.

**Configuration semantics**: `ensure()` directly receives a complete `OCRConfig` (or `None`). Request-level field composition is done once by the API route layer; all downstream layers (TaskManager / Pipeline / EngineManager) only accept complete snapshots and do not perform field-level merging.

**GPU selection**: `OCRConfig.gpu_id` defaults to `None` (auto). `EngineManager.ensure()` calls `docrestore.ocr.gpu_detect.pick_best_gpu()` at the entry point to resolve it to a concrete physical index by VRAM (descending), then passes it to ppocr-server and DeepSeek worker via `CUDA_VISIBLE_DEVICES`. The frontend fetches `GET /api/v1/gpus` (`{gpus, recommended}`) to render the selector; the "Auto (recommended)" option sends an empty string so the backend re-runs `pick_best_gpu` and remains authoritative.

### 5.2 DeepSeekOCR2Engine (ocr/deepseek_ocr2.py)

A **subprocess client** that communicates with `scripts/deepseek_ocr_worker.py` via the JSON Lines protocol. The backend does not directly depend on vLLM/torch.

```python
class DeepSeekOCR2Engine:
    def __init__(self, config: OCRConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._ready = False
        self._desync = False  # 协议失步标志

    async def initialize(self) -> None:
        """启动 worker 子进程（deepseek_ocr conda 环境），发送 initialize 命令"""

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        """发送 ocr 命令，worker 完成推理 + grounding + 裁剪，返回 PageOCR"""

    async def reocr_page(self, image_path: Path) -> str:
        """对整页重新 OCR（gap fill 用），返回清洗后的 markdown"""

    async def shutdown(self) -> None:
        """发送 shutdown 命令，终止子进程"""
```

**Communication protocol** (JSON Lines over stdin/stdout):
- Request: `{"cmd": "initialize", ...}` | `{"cmd": "ocr", ...}` | `{"cmd": "reocr_page", ...}` | `{"cmd": "shutdown"}`
- Response: `{"ok": true, "raw_text": "...", "regions": [...], ...}` | `{"ok": false, "error": "..."}`

**Protocol desync recovery**: Cancellation (CancelledError) can cause stdin/stdout misalignment. The `_desync` flag triggers an automatic worker restart before the next call.

**Worker internals** (`scripts/deepseek_ocr_worker.py`, runs in the deepseek_ocr conda environment):
- Loads vLLM AsyncLLMEngine + ImagePreprocessor
- OCR flow: preprocessing -> inference -> grounding parsing -> image cropping -> sidebar filtering
- Uses `asyncio.new_event_loop()` to drive the vLLM async API

## 6. Output Directory Structure

Each photo produces an independent directory (filenames come from the `OCR_*_FILENAME` constants in `ocr/base.py`):

```
{task_output}/{image_stem}_OCR/
├── result_ori.mmd          # Raw output (with grounding tags) -> OCR_RAW_RESULT_FILENAME
├── result.mmd              # Grounding parsed, images cropped and replaced -> OCR_RESULT_FILENAME
├── debug_coords.jsonl      # Sidebar filter debug coordinates (optional) -> OCR_DEBUG_COORDS_FILENAME
├── result_with_boxes.jpg   # Layout visualization
└── images/                 # Cropped illustrations (0.jpg, 1.jpg, ...)
```

### 5.4 ColumnFilter (ocr/column_filter.py)

The sidebar detection and filtering module. Input photos are taken from online documents (Yuque, etc.) whose pages may include a left navigation sidebar and a right outline column. If mixed into the main content, these interfere with clustering and deduplication.

```python
class ColumnFilter:
    def __init__(self, min_sidebar_count: int = 5) -> None: ...

    def parse_grounding_regions(self, raw_text: str) -> list[GroundingRegion]:
        """从 result_ori.mmd 解析 grounding 区域及文本"""

    def detect_boundaries(self, regions: list[GroundingRegion]) -> ColumnBoundaries:
        """自适应检测列边界（阈值来自 ColumnFilterThresholds：
        左栏 x1<left_candidate_max_x1 且 x2<=left_candidate_max_x2；
        右栏 x1>=right_candidate_min_x1 且 width<right_candidate_max_width）"""

    def filter_regions(self, regions, boundaries) -> list[GroundingRegion]:
        """过滤侧栏区域，只保留正文"""

    def rebuild_text(self, content_regions: list[GroundingRegion]) -> str:
        """从正文区域重建 grounding 文本（保留标签格式）"""

    def needs_reocr(self, total_count, content_count) -> bool:
        """正文占比 <20% 或 >95% → 需要裁剪重跑"""

    def compute_crop_box(self, boundaries, image_width, image_height) -> tuple:
        """归一化坐标 → 像素坐标裁剪框"""
```

**Hybrid strategy**:
1. First OCR pass -> grounding coordinates -> detect sidebars -> rebuild output using grounding text from main-content regions
2. If the main-content region ratio is abnormal -> crop the image to the main-content area and re-run OCR

**Configuration** (OCRConfig):
- `enable_column_filter: bool = False` -- Enable coordinate-based sidebar filtering (PaddleOCR precision is insufficient; disabled by default)
- `column_filter_min_sidebar: int = 5` -- Minimum sidebar region count to trigger filtering
- `column_filter_thresholds: ColumnFilterThresholds` -- Specific thresholds (see table below)

**ColumnFilterThresholds** (all coordinates normalized to `0..coord_range`):

| Field | Default | Description |
|---|---|---|
| `chrome_y_threshold` | `80` | Browser chrome area y-axis upper bound; avoids the browser tab bar |
| `min_sidebar_y_spread` | `300` | Minimum vertical span for candidate regions; excludes labels clustered at the top |
| `left_candidate_max_x1` / `left_candidate_max_x2` | `100` / `220` | Left-column candidate recognition (x1 upper bound + x2 upper bound) |
| `right_candidate_min_x1` / `right_candidate_max_width` | `800` / `200` | Right-column candidate recognition (x1 lower bound + max width) |
| `left_boundary_padding` / `right_boundary_padding` | `20` / `20` | Boundary expansion in pixels |
| `left_filter_padding` | `40` | Extra left-side filtering padding |
| `full_width_threshold` | `700` | Minimum width to be considered a full-width element |
| `main_content_ratio_threshold` | `0.3` | Column-split validation threshold |
| `min_validation_count` | `3` | Minimum sample count for column-split validation |
| `content_min_ratio` / `content_max_ratio` | `0.2` / `0.95` | Main-content ratio bounds that trigger a crop-and-rerun |
| `coord_range` | `999` | Normalized coordinate upper bound (matches model convention) |

**Additional output files**:
- `result_ori_filtered.mmd` -- Grounding text after filtering (when the filtering path is taken)
- `result_ori_reocr.mmd` -- Raw output from the crop-and-rerun (when the rerun path is taken)

### 5.5 PaddleOCREngine (ocr/paddle_ocr.py)

Calls PaddleOCR in a separate conda environment via subprocess for environment isolation.

**Unified architecture of the two engines**:

| | DeepSeek-OCR-2 | PaddleOCR |
|---|---|---|
| Execution mode | subprocess worker (deepseek_ocr env) | subprocess worker (ppocr_client env) + genai_server (ppocr_vlm env) |
| Environment isolation | conda env `deepseek_ocr` | Two conda envs (ppocr_vlm + ppocr_client) |
| GPU usage | vLLM loaded inside worker | genai_server manages independently |
| Startup | EngineManager auto-starts worker | EngineManager auto-starts ppocr-server + worker |
| Communication protocol | JSON Lines over stdin/stdout | JSON Lines over stdin/stdout |

**Automatic ppocr-server management**: EngineManager automatically starts the ppocr genai_server subprocess when switching to PaddleOCR -- no need to manually run `start.sh ppocr-server`. It is automatically shut down when switching to another engine.

**Two operating modes**:

1. **Server mode** (recommended, EngineManager default): `paddle_server_url` is non-empty
   - EngineManager auto-starts the genai_server subprocess
   - Worker uses `PaddleOCRVL(vl_rec_backend="vllm-server", vl_rec_server_url=...)`
   - VLM inference is delegated to genai_server; the worker only performs layout analysis

2. **Local mode** (compatibility): `paddle_server_url` is empty and `paddle_server_python` is empty
   - Worker uses `PaddleOCRVL()` for local inference
   - No genai_server needed
   - Suitable for simple single-machine scenarios

```python
class PaddleOCREngine:
    def __init__(self, config: OCRConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None

    async def initialize(self) -> None:
        """启动 worker 子进程，传递 server 配置（scripts/paddle_ocr_worker.py）"""

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        """
        通过 JSON Lines 协议与 worker 通信：
        1. 发送 {"cmd": "ocr", "image_path": "...", "output_dir": "..."}
        2. worker 调用 PaddleOCRVL.predict()（自动走 server 或本地）
        3. worker 整理输出目录（重命名 imgs/ → images/，重命名图片为 0.jpg, 1.jpg）
        4. 返回 PageOCR（从 markdown 解析图片引用构造 regions）
        """

    async def shutdown(self) -> None:
        """发送 shutdown 命令，终止子进程"""
```

**Communication protocol** (JSON Lines over stdin/stdout):
- Request: `{"cmd": "initialize", "server_url": "...", "server_model_name": "..."}` | `{"cmd": "ocr", ...}` | `{"cmd": "shutdown"}`
- Response: `{"ok": true, ...}` | `{"ok": false, "error": "..."}`

**Configuration** (OCRConfig):

| Field | Default | Description |
|------|--------|------|
| `paddle_python` | `""` | Python path for the ppocr_client conda environment (auto-detected) |
| `paddle_server_python` | `""` | Python for the ppocr_vlm conda environment (used by EngineManager to start the server; auto-detected) |
| `paddle_server_url` | `""` | genai_server URL (non-empty enables server mode; when empty, auto-constructed from `host/port/api_version`) |
| `paddle_server_host` | `"localhost"` | Hostname for auto-constructed URL |
| `paddle_server_port` | `8119` | ppocr-server port |
| `paddle_server_api_version` | `"v1"` | OpenAI API version segment compatible with the server |
| `paddle_server_startup_timeout` | `300` | ppocr-server total startup timeout (seconds) |
| `paddle_server_connect_timeout` | `2.0` | Single port reachability probe timeout (seconds) |
| `paddle_server_poll_interval` | `2.0` | Startup readiness polling interval (seconds) |
| `paddle_server_shutdown_timeout` | `10.0` | SIGTERM wait during shutdown (escalates to SIGKILL on timeout) |
| `paddle_server_model_name` | `"PaddleOCR-VL-1.5-0.9B"` | Server-side model name |
| `paddle_ocr_timeout` | `300` | Per-image OCR timeout (seconds) |
| `paddle_restart_interval` | `20` | Restart worker every N images (recommend `0` in server mode) |
| `paddle_worker_script` | `""` | Worker script path; empty string falls back to the repository default (supports absolute paths) |
| `paddle_min_image_size` | `64` | Filter out small icons with width or height below this value (px) |
| `worker_terminate_timeout` | `5.0` | Worker process terminate wait timeout (shared by paddle/deepseek) |
| `worker_stdio_buffer_bytes` | `16 * 1024 * 1024` | Worker subprocess stdio single-line buffer limit; large-image grounding JSON lines may exceed asyncio's default 64 KB -- increased to avoid `LimitOverrunError` (shared by both engines) |

**DeepSeek-OCR-2 configuration** (OCRConfig):

| Field | Default | Description |
|------|--------|------|
| `deepseek_python` | `""` | Python path for the deepseek_ocr conda environment (auto-detected) |
| `deepseek_ocr_timeout` | `600` | Per-image OCR timeout (seconds; DeepSeek inference is slower) |
| `deepseek_worker_script` | `""` | Worker script path; empty string falls back to the repository default (supports absolute paths) |
| `model_path` | `"models/DeepSeek-OCR-2"` | Local weights path (not a HuggingFace repo id) |
| `gpu_memory_utilization` | `0.75` | vLLM GPU memory utilization ratio |
| `max_model_len` / `max_tokens` | `8192` / `8192` | vLLM context length and generation length |
| `base_size` / `crop_size` | `1024` / `768` | Global view size / local tile size |
| `min_crops` / `max_crops` | `2` / `6` | Dynamic tile split count range |
| `normalize_mean` / `normalize_std` | `(0.5, 0.5, 0.5)` / `(0.5, 0.5, 0.5)` | Post-ToTensor normalization parameters (must match backbone training) |
| `ngram_size` / `ngram_window_size` | `20` / `90` | Loop suppression n-gram length / sliding window size |
| `ngram_whitelist_token_ids` | `{128821, 128822}` | Whitelisted tokens (table tags `<td>`/`<tr>` etc. excluded from loop suppression) |
| `prompt` | `"<image>\nFree OCR.\n<\|grounding\|>Convert the document to markdown."` | Prompt |

**Output adaptation**:
- PaddleOCR raw output: `{stem}.md` + `imgs/*.jpg`
- Adapted to OCREngine contract: `result.mmd` + `images/0.jpg, 1.jpg, ...`
- Image references in markdown rewritten from `imgs/` to `images/`

**Limitations**:
- Does not support `reocr_page()` (PaddleOCR does not need re-OCR for gap filling)
- Does not support `reset_cache()` (no prefix cache mechanism)

### 5.6 OCR Router (ocr/router.py)

An OCR engine factory function that creates the corresponding engine instance by model identifier. Called internally by EngineManager.

```python
def create_engine(model: str, config: OCRConfig) -> OCREngine:
    """根据模型标识符创建引擎。

    支持的模型：
    - "deepseek/ocr-2" 或 "deepseek" → DeepSeekOCR2Engine
    - "paddle-ocr/ppocr-v4" 或 "paddle-ocr" → PaddleOCREngine
    """
```

**Note**: Engine selection and switching is handled by EngineManager; the router is purely a factory function.

## 7. Design Decisions

### 7.1 Why use Free OCR + grounding combination?
- Free OCR provides high-quality text recognition
- Grounding provides bounding boxes, enabling illustration cropping
- The combination captures the advantages of both

### 7.2 Why use a unified subprocess worker architecture?
- PaddleOCR and DeepSeek-OCR-2 have incompatible dependencies (vllm 0.8.5 vs 0.9+, torch 2.6 vs 2.8) and cannot share a conda environment
- The unified subprocess architecture makes the backend a lightweight coordinator that does not directly depend on torch/vllm
- Both engines use the same JSON Lines protocol, keeping code patterns consistent
- EngineManager switches engines on demand; only one occupies the GPU at any time; frontend selection takes effect immediately

### 7.3 Why does PaddleOCR need a separate server?
- PaddleOCR's VLM inference (GPU-intensive) is managed by genai_server; the worker only performs layout analysis
- Server/client separation allows independent deployment to different GPUs
- EngineManager automatically manages the server lifecycle; users do not need to start it manually

### 7.4 Protocol desync recovery
- Cancellation (asyncio.CancelledError) can cause JSON Lines protocol desync (a request was written but the response was not read)
- The `_desync` flag records the desync state and triggers an automatic worker restart before the next call to restore synchronization
- Applies to both the DeepSeek and PaddleOCR engines
