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

# OCR 层（ocr/）

## 1. 职责

将文档照片转换为含 grounding 标签的 markdown 文本，同时裁剪插图区域。模型常驻 GPU，支持连续处理多张照片。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `ocr/base.py` | `OCREngine` Protocol + `WorkerBackedOCREngine` 公共基类 + 契约文件名常量 |
| `ocr/engine_manager.py` | **引擎生命周期管理器**（按需切换，自动管理 ppocr-server） |
| `ocr/router.py` | OCR 引擎工厂（根据模型标识符创建引擎） |
| `ocr/deepseek_ocr2.py` | DeepSeek-OCR-2 **子进程客户端**（JSON Lines IPC） |
| `ocr/paddle_ocr.py` | PaddleOCR 子进程客户端（JSON Lines IPC） |
| `ocr/column_filter.py` | 侧栏检测与过滤（grounding 坐标分析） |
| `ocr/preprocessor.py` | 图片预处理（动态分辨率 + tile 切分，仅 worker 内使用） |
| `ocr/ngram_filter.py` | NoRepeatNGram 循环抑制（仅 worker 内使用） |
| `scripts/deepseek_ocr_worker.py` | DeepSeek-OCR-2 worker 进程（vLLM 推理，独立 conda 环境） |
| `scripts/paddle_ocr_worker.py` | PaddleOCR worker 进程（布局分析 + server 调用，独立 conda 环境） |

## 3. 对外接口

### 3.1 OCREngine Protocol（ocr/base.py）

其他模块（Pipeline）通过此接口调用 OCR 层。

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

**契约文件名常量**（`ocr/base.py`，与 worker 脚本共用）：

```python
OCR_RESULT_FILENAME = "result.mmd"           # grounding 已解析、图片已裁剪替换的 markdown
OCR_RAW_RESULT_FILENAME = "result_ori.mmd"   # 原始输出（含 grounding 标签）
OCR_DEBUG_COORDS_FILENAME = "debug_coords.jsonl"
```

> worker 脚本（`scripts/*_ocr_worker.py`）运行在独立 conda 环境无法 import 后端模块，所以同名字面量需要与这些常量**手工同步**。

**调用约定**：
- 必须先 `initialize()` 再调用 `ocr()` / `ocr_batch()`
- `initialize(on_progress)` 的两类回调不要混淆：`ProgressFn = Callable[[str], None]`（模型加载阶段推文字消息），`ocr_batch` 的 `on_progress` 是 `Callable[[int, int], None]`（逐张进度）
- `output_dir` 由 Pipeline 传入，`ocr()` 在其下创建 `{image_stem}_OCR/` 子目录
- `ocr()` 返回的 `PageOCR.raw_text` 含 grounding 标签，`cleaned_text` 为空
- `ocr()` 内部完成 grounding 解析 + 图片裁剪，结果写入 `PageOCR.output_dir`
- `ocr_batch()` 逐张调用，不做批量推理（需要中间结果做滚动合并）

### 3.2 WorkerBackedOCREngine（ocr/base.py）

DeepSeek/Paddle 两类引擎都通过独立 conda 环境的 subprocess worker 实现，`WorkerBackedOCREngine(ABC)` 抽出公共骨架：

- worker 脚本定位（`_find_worker_script` 支持绝对路径 + 仓库相对回退）
- subprocess 启动（使用 `OCRConfig.worker_stdio_buffer_bytes` 作为 stdio 缓冲上限，默认 16MB，避免大图 grounding JSON 触发 `LimitOverrunError`）
- JSON Lines 命令往返（`_send_command`）与协议失步恢复（`_desync` 标志）
- `ocr_batch` / `shutdown` / `_restart_worker` 默认实现

子类必须实现：`engine_name` / `worker_script_path` 类属性、`_get_python_path` / `_get_timeout` / `_build_subprocess_env` / `_build_init_cmd` / `_terminate_process` / `ocr`。

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `models.py` | `PageOCR`, `Region` |
| `pipeline/config.py` | `OCRConfig` |

不依赖其他处理层模块。

## 5. 内部实现

### 5.1 EngineManager（ocr/engine_manager.py）

引擎生命周期管理器，核心组件。按需切换 OCR 引擎（PaddleOCR ↔ DeepSeek-OCR-2），同一时刻只有一个引擎占用 GPU。

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

**ppocr-server 自动管理**：
- PaddleOCR 需要先启动 ppocr genai_server。EngineManager 在切换到 PaddleOCR 时自动以子进程启动 server，切走时自动关闭。
- 启动后通过 TCP 端口轮询健康检查，探测超时 `paddle_server_connect_timeout`（默认 `2.0s`），轮询间隔 `paddle_server_poll_interval`（默认 `2.0s`）；总超时 `paddle_server_startup_timeout`。
- 关闭策略：SIGTERM → wait(`paddle_server_shutdown_timeout`，默认 `10.0s`) → SIGKILL。

**配置语义**：`ensure()` 直接接收完整 `OCRConfig`（或 `None`）。请求级字段合成由 API 路由层一次性完成，下游各层（TaskManager / Pipeline / EngineManager）只认完整快照，不再做字段级合并。

**GPU 选择**：`OCRConfig.gpu_id` 默认 `None`，表示"自动"。`EngineManager.ensure()` 入口会调 `docrestore.ocr.gpu_detect.pick_best_gpu()` 按显存降序落地成具体物理索引，再通过 `CUDA_VISIBLE_DEVICES` 传给 ppocr-server 和 DeepSeek worker。前端任务表单调用 `GET /api/v1/gpus`（返回 `gpus + recommended`）渲染下拉；"自动（推荐）"项传空字符串，后端再次调 `pick_best_gpu` 保持权威。

### 5.2 DeepSeekOCR2Engine（ocr/deepseek_ocr2.py）

**子进程客户端**，通过 JSON Lines 协议与 `scripts/deepseek_ocr_worker.py` 通信。后端不直接依赖 vLLM/torch。

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

**通信协议**（JSON Lines over stdin/stdout）：
- 请求：`{"cmd": "initialize", ...}` | `{"cmd": "ocr", ...}` | `{"cmd": "reocr_page", ...}` | `{"cmd": "shutdown"}`
- 响应：`{"ok": true, "raw_text": "...", "regions": [...], ...}` | `{"ok": false, "error": "..."}`

**协议失步恢复**：取消操作（CancelledError）可能导致 stdin/stdout 错位，`_desync` 标志触发下次调用前自动重启 worker。

**worker 内部**（`scripts/deepseek_ocr_worker.py`，运行在 deepseek_ocr conda 环境）：
- 加载 vLLM AsyncLLMEngine + ImagePreprocessor
- OCR 流程：预处理 → 推理 → grounding 解析 → 图片裁剪 → 侧栏过滤
- 使用 `asyncio.new_event_loop()` 驱动 vLLM async API

## 6. 输出目录结构

每张照片产出独立目录（文件名来自 `ocr/base.py` 中的 `OCR_*_FILENAME` 常量）：

```
{task_output}/{image_stem}_OCR/
├── result_ori.mmd          # 原始输出（含 grounding 标签）→ OCR_RAW_RESULT_FILENAME
├── result.mmd              # grounding 已解析、图片已裁剪替换的 markdown → OCR_RESULT_FILENAME
├── debug_coords.jsonl      # 侧栏过滤调试坐标（可选）→ OCR_DEBUG_COORDS_FILENAME
├── result_with_boxes.jpg   # 布局可视化
└── images/                 # 裁剪的插图（0.jpg, 1.jpg, ...）
```

### 5.4 ColumnFilter（ocr/column_filter.py）

侧栏检测与过滤模块。输入照片为拍摄的在线文档（语雀等），页面可能包含左栏导航目录和右栏大纲，混入正文会干扰聚类和去重。

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

**混合策略**：
1. 第一遍 OCR → grounding 坐标 → 检测侧栏 → 用正文区域的 grounding 文本重建输出
2. 如果正文区域占比异常 → 裁剪图片到正文区域，重新 OCR

**配置项**（OCRConfig）：
- `enable_column_filter: bool = False` — 启用坐标侧栏过滤（PaddleOCR 精度不足，默认关）
- `column_filter_min_sidebar: int = 5` — 最少侧栏区域数才触发过滤
- `column_filter_thresholds: ColumnFilterThresholds` — 具体阈值（见下表）

**ColumnFilterThresholds**（所有坐标归一化到 `0..coord_range`）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `chrome_y_threshold` | `80` | 浏览器 Chrome 区域 y 轴上界，避开浏览器标签栏 |
| `min_sidebar_y_spread` | `300` | 候选区域最小纵向跨度，排除聚集顶部的标签 |
| `left_candidate_max_x1` / `left_candidate_max_x2` | `100` / `220` | 左栏候选识别（x1 上界 + x2 上界） |
| `right_candidate_min_x1` / `right_candidate_max_width` | `800` / `200` | 右栏候选识别（x1 下界 + 最大宽度） |
| `left_boundary_padding` / `right_boundary_padding` | `20` / `20` | 边界扩展像素 |
| `left_filter_padding` | `40` | 左侧过滤额外 padding |
| `full_width_threshold` | `700` | 视为全宽元素的最小宽度 |
| `main_content_ratio_threshold` | `0.3` | 分栏验证阈值 |
| `min_validation_count` | `3` | 分栏验证最少样本数 |
| `content_min_ratio` / `content_max_ratio` | `0.2` / `0.95` | 正文占比触发裁剪重跑的边界 |
| `coord_range` | `999` | 归一化坐标上界（与模型约定一致） |

**额外输出文件**：
- `result_ori_filtered.mmd` — grounding 过滤后的文本（当走过滤路径时）
- `result_ori_reocr.mmd` — 裁剪重跑的原始输出（当走重跑路径时）

### 5.5 PaddleOCREngine（ocr/paddle_ocr.py）

通过 subprocess 调用独立 conda 环境中的 PaddleOCR，实现环境隔离。

**两个引擎的统一架构**：

| | DeepSeek-OCR-2 | PaddleOCR |
|---|---|---|
| 运行方式 | subprocess worker（deepseek_ocr env） | subprocess worker（ppocr_client env）+ genai_server（ppocr_vlm env） |
| 环境隔离 | conda env `deepseek_ocr` | 两个 conda env（ppocr_vlm + ppocr_client） |
| GPU 占用 | worker 内部加载 vLLM | genai_server 独立管理 |
| 启动方式 | EngineManager 自动启动 worker | EngineManager 自动启动 ppocr-server + worker |
| 通信协议 | JSON Lines over stdin/stdout | JSON Lines over stdin/stdout |

**ppocr-server 自动管理**：EngineManager 在切换到 PaddleOCR 时自动启动 ppocr genai_server 子进程，无需手动执行 `start.sh ppocr-server`。切换到其他引擎时自动关闭。

**两种运行模式**：

1. **Server 模式**（推荐，EngineManager 默认）：`paddle_server_url` 非空
   - EngineManager 自动启动 genai_server 子进程
   - worker 中 `PaddleOCRVL(vl_rec_backend="vllm-server", vl_rec_server_url=...)`
   - VLM 推理交给 genai_server，worker 只做布局分析

2. **本地模式**（兼容）：`paddle_server_url` 为空且 `paddle_server_python` 为空
   - worker 中 `PaddleOCRVL()` 本地推理
   - 不需要 genai_server
   - 适合单机简单场景

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

**通信协议**（JSON Lines over stdin/stdout）：
- 请求：`{"cmd": "initialize", "server_url": "...", "server_model_name": "..."}` | `{"cmd": "ocr", ...}` | `{"cmd": "shutdown"}`
- 响应：`{"ok": true, ...}` | `{"ok": false, "error": "..."}`

**配置项**（OCRConfig）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `paddle_python` | `""` | ppocr_client conda 环境的 python 路径（自动检测） |
| `paddle_server_python` | `""` | ppocr_vlm conda 环境的 python（EngineManager 启动 server 用，自动检测） |
| `paddle_server_url` | `""` | genai_server URL（非空启用 server 模式；为空时按 `host/port/api_version` 自动拼装） |
| `paddle_server_host` | `"localhost"` | 自动构造 URL 的主机名 |
| `paddle_server_port` | `8119` | ppocr-server 端口 |
| `paddle_server_api_version` | `"v1"` | server 兼容的 OpenAI API 版本段 |
| `paddle_server_startup_timeout` | `300` | ppocr-server 启动总超时（秒） |
| `paddle_server_connect_timeout` | `2.0` | 单次端口可达性探测超时（秒） |
| `paddle_server_poll_interval` | `2.0` | 启动就绪轮询间隔（秒） |
| `paddle_server_shutdown_timeout` | `10.0` | 关闭阶段 SIGTERM 等待（超时后升级 SIGKILL） |
| `paddle_server_model_name` | `"PaddleOCR-VL-1.5-0.9B"` | server 端模型名称 |
| `paddle_ocr_timeout` | `300` | 单张 OCR 超时（秒） |
| `paddle_restart_interval` | `20` | 每 N 张重启 worker（server 模式建议 `0`） |
| `paddle_worker_script` | `""` | worker 脚本路径；空串回退仓库内默认（支持绝对路径） |
| `paddle_min_image_size` | `64` | 过滤宽或高小于此值的小图标（px） |
| `worker_terminate_timeout` | `5.0` | worker 进程 terminate 等待超时（paddle/deepseek 共用） |
| `worker_stdio_buffer_bytes` | `16 * 1024 * 1024` | worker 子进程 stdio 单行缓冲上限；大图 grounding JSON 单行可能超 asyncio 默认 64KB，放大避免 `LimitOverrunError`（两引擎共用） |

**DeepSeek-OCR-2 配置项**（OCRConfig）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `deepseek_python` | `""` | deepseek_ocr conda 环境的 python 路径（自动检测） |
| `deepseek_ocr_timeout` | `600` | 单张 OCR 超时（秒，DeepSeek 推理较慢） |
| `deepseek_worker_script` | `""` | worker 脚本路径；空串回退仓库内默认（支持绝对路径） |
| `model_path` | `"models/DeepSeek-OCR-2"` | 本地权重路径（非 HuggingFace repo id） |
| `gpu_memory_utilization` | `0.75` | vLLM GPU 显存占比 |
| `max_model_len` / `max_tokens` | `8192` / `8192` | vLLM 上下文与生成长度 |
| `base_size` / `crop_size` | `1024` / `768` | 全局视图尺寸 / 局部 tile 尺寸 |
| `min_crops` / `max_crops` | `2` / `6` | 动态 tile 切分数量范围 |
| `normalize_mean` / `normalize_std` | `(0.5, 0.5, 0.5)` / `(0.5, 0.5, 0.5)` | ToTensor 后的归一化参数（需与骨干网络训练一致） |
| `ngram_size` / `ngram_window_size` | `20` / `90` | 循环抑制 ngram 长度 / 滑窗大小 |
| `ngram_whitelist_token_ids` | `{128821, 128822}` | 白名单 token（表格标签 `<td>`/`<tr>` 等不参与循环抑制） |
| `prompt` | `"<image>\nFree OCR.\n<\|grounding\|>Convert the document to markdown."` | 提示词 |

**输出适配**：
- PaddleOCR 原始输出：`{stem}.md` + `imgs/*.jpg`
- 适配为 OCREngine 约定：`result.mmd` + `images/0.jpg, 1.jpg, ...`
- markdown 中的图片引用从 `imgs/` 替换为 `images/`

**限制**：
- 不支持 `reocr_page()`（PaddleOCR 不需要 gap fill 的 re-OCR）
- 不支持 `reset_cache()`（无 prefix cache 机制）

### 5.6 OCR Router（ocr/router.py）

OCR 引擎工厂函数，根据模型标识符创建对应引擎实例。EngineManager 内部调用此函数。

```python
def create_engine(model: str, config: OCRConfig) -> OCREngine:
    """根据模型标识符创建引擎。

    支持的模型：
    - "deepseek/ocr-2" 或 "deepseek" → DeepSeekOCR2Engine
    - "paddle-ocr/ppocr-v4" 或 "paddle-ocr" → PaddleOCREngine
    """
```

**注意**：引擎选择和切换由 EngineManager 负责，router 只是工厂函数。

## 7. 设计决策

### 7.1 为什么用 Free OCR + grounding 组合？
- Free OCR 提供高质量文本识别
- grounding 提供 bbox，可裁剪插图
- 组合使用兼得两者优势

### 7.2 为什么统一为子进程 worker 架构？
- PaddleOCR 和 DeepSeek-OCR-2 的依赖不兼容（vllm 0.8.5 vs 0.9+，torch 2.6 vs 2.8），不能合并 conda 环境
- 统一子进程架构使后端成为轻量协调器，不直接依赖 torch/vllm
- 两个引擎使用相同的 JSON Lines 协议，代码模式一致
- EngineManager 按需切换引擎，同一时刻只有一个占 GPU，前端选择即时生效

### 7.3 为什么 PaddleOCR 额外需要独立 server？
- PaddleOCR 的 VLM 推理（GPU 密集）由 genai_server 管理，worker 只做布局分析
- server/client 分离允许独立部署到不同 GPU
- EngineManager 自动管理 server 生命周期，用户无需手动启动

### 7.4 协议失步恢复
- 取消操作（asyncio.CancelledError）可能导致 JSON Lines 协议失步（写了请求但没读响应）
- `_desync` 标志位记录失步状态，下次调用前自动重启 worker 恢复同步
- 适用于 DeepSeek 和 PaddleOCR 两个引擎