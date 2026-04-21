# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pipeline 配置（pydantic BaseModel，嵌套结构）

贯穿所有层（API → TaskManager → DB → Pipeline），消除以往 dict[str, object]
的"无类型跳板"。合并请求级覆盖使用 `config.model_copy(update=...)`，
序列化使用 `model_dump_json()` / `model_validate_json()`。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ColumnFilterThresholds(BaseModel):
    """侧栏检测与过滤阈值（grounding 坐标归一化到 0..coord_range）。

    这些参数与图像分辨率/浏览器截图布局强相关，值来自人工校准，
    抽出来方便根据采集设备差异进行调参。
    """

    # 浏览器 Chrome 区域上界（y 轴）
    chrome_y_threshold: int = 80
    # 候选区域最小纵向跨度（排除聚集在顶部的浏览器标签）
    min_sidebar_y_spread: int = 300

    # 左栏候选识别
    left_candidate_max_x1: int = 100
    left_candidate_max_x2: int = 220

    # 右栏候选识别
    right_candidate_min_x1: int = 800
    right_candidate_max_width: int = 200

    # 边界扩展
    left_boundary_padding: int = 20
    right_boundary_padding: int = 20
    left_filter_padding: int = 40

    # 分栏验证
    full_width_threshold: int = 700  # 视为全宽元素的最小宽度
    main_content_ratio_threshold: float = 0.3
    min_validation_count: int = 3

    # 正文占比
    content_min_ratio: float = 0.2
    content_max_ratio: float = 0.95

    # 归一化坐标范围上界
    coord_range: int = 999


class OCRConfig(BaseModel):
    """OCR 引擎配置"""

    model: str = "paddle-ocr/ppocr-v4"  # 统一模型标识符
    model_path: str = "models/DeepSeek-OCR-2"  # DeepSeek-OCR-2 本地权重路径
    gpu_memory_utilization: float = 0.75
    max_model_len: int = 8192
    max_tokens: int = 8192
    # 图片预处理
    base_size: int = 1024  # 全局视图尺寸
    crop_size: int = 768  # 局部 tile 尺寸
    max_crops: int = 6
    min_crops: int = 2
    # 归一化参数（与模型训练时保持一致；不同骨干网络可能不同）
    normalize_mean: tuple[float, float, float] = (0.5, 0.5, 0.5)
    normalize_std: tuple[float, float, float] = (0.5, 0.5, 0.5)
    # 循环抑制
    ngram_size: int = 20
    ngram_window_size: int = 90
    ngram_whitelist_token_ids: set[int] = Field(
        default_factory=lambda: {128821, 128822}
    )
    # prompt
    prompt: str = (
        "<image>\nFree OCR.\n"
        "<|grounding|>Convert the document to markdown."
    )
    # 侧栏过滤
    enable_column_filter: bool = False  # 启用坐标侧栏过滤（PaddleOCR 精度不足，默认关）
    column_filter_min_sidebar: int = 5  # 最少侧栏区域数才触发过滤
    column_filter_thresholds: ColumnFilterThresholds = Field(
        default_factory=ColumnFilterThresholds,
    )

    # GPU 选择（两个引擎通用，前端可选）
    # None 表示 "自动"：engine_manager 组装 CUDA_VISIBLE_DEVICES 时会调
    # gpu_detect.pick_best_gpu() 选显存最大的一张，保持跨机器可移植。
    # 显式传入如 "0"/"1" 时以配置为准。
    gpu_id: str | None = None

    # === 两引擎共有的 vLLM 优化参数 ===
    # DeepSeek 进程内直接透传到 AsyncEngineArgs；
    # PaddleOCR 通过 scripts/bench_ocr.py 生成的 backend_config YAML 注入
    # ppocr-server。None 表示沿用 vLLM 默认值，不主动覆盖。
    vllm_enforce_eager: bool | None = None  # 显式控制 CUDA Graph 启用
    vllm_block_size: int | None = None  # KV cache block 大小（默认 16，常用 256）
    vllm_swap_space_gb: float | None = None  # CPU swap GiB（默认 4，OCR 场景可 0）
    vllm_disable_mm_preprocessor_cache: bool = False  # OCR 每张图不同，缓存命中率 0
    vllm_disable_log_stats: bool = False  # 关闭 vLLM 内部统计日志

    # === 批量推理 + 显存监控（方案 1 / performance_toolkit）===
    # OCR 批大小：Pipeline 一次向 worker 提交 N 张图，worker 内 asyncio.gather
    # 并发处理，vLLM 自动 continuous batching，CPU 后处理与下一批 GPU 天然 overlap。
    # < 2 回退逐张处理（保留旧路径，便于对比或兜底）。
    ocr_batch_size: int = 4
    # 启用 worker 内后台 GPU 监控 task（nvidia-smi 外部采样仍由 gpu_sampler.py 完成，
    # 这里监控的是 Python 进程内 torch.cuda 视角 —— free / allocated / reserved /
    # frag_ratio —— 方便定位显存碎片化）。
    gpu_monitor_enable: bool = True
    gpu_monitor_interval_s: float = 1.0  # 采样周期（秒）
    # free 显存低于该阈值时 worker 主动调用 torch.cuda.empty_cache() 回收碎片，
    # 并写 WARN 日志供父进程展示。
    gpu_memory_safety_margin_mib: int = 1024

    # === PaddleOCR 专用（model="paddle-ocr/..." 时生效）===
    paddle_python: str = ""  # PaddleOCR conda 环境的 python 路径
    paddle_ocr_timeout: int = 300  # 单张 OCR 超时（秒）
    paddle_restart_interval: int = 20  # 每 N 张图片重启 worker（0 禁用）
    # worker 脚本路径（空串时使用默认仓库内路径）
    paddle_worker_script: str = ""
    # ppocr-server 的 --backend_config YAML 路径（空串时用 PaddleOCR 默认配置）
    paddle_server_backend_config: str = ""

    # PaddleOCR server 模式（paddle_server_url 非空时启用）
    paddle_server_url: str = ""  # 如 "http://localhost:8119/v1"
    paddle_server_model_name: str = "PaddleOCR-VL-1.5-0.9B"
    paddle_min_image_size: int = 64  # 过滤宽或高小于此值的小图标（px）

    # ppocr-server 自动管理（EngineManager 控制）
    paddle_server_python: str = ""  # ppocr_vlm conda 环境的 python（启动 server 用）
    paddle_server_host: str = "localhost"  # 自动构造 URL 时使用的主机
    paddle_server_port: int = 8119  # ppocr-server 端口
    paddle_server_api_version: str = "v1"  # server 兼容的 OpenAI API 版本段
    paddle_server_startup_timeout: int = 300  # server 启动超时（秒，慢速 GPU 需要更长）
    paddle_server_shutdown_timeout: float = 10.0  # SIGTERM 等待超时，超时升级 SIGKILL
    paddle_server_connect_timeout: float = 2.0  # 单次端口可达性探测超时
    paddle_server_poll_interval: float = 2.0  # 启动就绪轮询间隔
    # worker 进程 terminate 等待超时（paddle/deepseek 共用）
    worker_terminate_timeout: float = 5.0
    # worker 子进程 stdio 单行缓冲上限（默认 16MB）
    # 大图 grounding JSON 单行可能超过 asyncio 默认 64KB，需放大避免 LimitOverrunError
    worker_stdio_buffer_bytes: int = 16 * 1024 * 1024

    def build_default_paddle_server_url(self) -> str:
        """根据 host/port/api_version 拼装 server URL（供 auto-configure 使用）。"""
        return (
            f"http://{self.paddle_server_host}:"
            f"{self.paddle_server_port}/{self.paddle_server_api_version}"
        )

    # === DeepSeek-OCR-2 专用（model="deepseek/..." 时生效）===
    deepseek_python: str = ""  # deepseek_ocr conda 环境的 python 路径
    deepseek_ocr_timeout: int = 600  # 单张 OCR 超时（秒，DeepSeek 推理较慢）
    # worker 脚本路径（空串时使用默认仓库内路径）
    deepseek_worker_script: str = ""


class DedupConfig(BaseModel):
    """去重合并配置"""

    similarity_threshold: float = 0.8  # 行级模糊匹配阈值
    overlap_context_lines: int = 3  # 保留给 LLM 的重叠上下文行数
    search_ratio: float = 0.7  # 取 A 尾部和 B 头部的比例（文档照片重叠通常较大）

    # 跨页频率过滤（文本级侧栏去除）
    repeated_line_threshold: float = 0.5  # 行出现页比例 ≥ 此值视为噪声
    repeated_line_min_pages: int = 4  # 总页数 < 此值时跳过（样本不足）
    repeated_line_min_block: int = 3  # 连续噪声行最小块大小（防误删孤立重复行）


class LLMConfig(BaseModel):
    """LLM 精修配置"""

    provider: str = "cloud"  # "cloud" | "local"
    model: str = ""  # litellm 模型名
    api_base: str = ""  # 自定义 API 地址，为空用默认
    api_key: str = ""  # 为空则由 litellm 从环境变量自动读取
    max_chars_per_segment: int = 8000  # 分段上限（中文字符 token 密度高，需保守）
    segment_overlap_lines: int = 5
    max_retries: int = 2
    timeout: int = 600  # 单次请求超时（秒），慢速中转站需要更大值
    enable_final_refine: bool = True  # 分段精修后是否做整篇文档级精修
    enable_gap_fill: bool = True  # 检测到 gap 时是否尝试 re-OCR 自动补充
    # 截断检测：输出行数少于输入 * (1 - ratio) 时视为可能被截断
    truncation_ratio_threshold: float = 0.3
    # 输入行数少于此值时不触发截断启发式（样本太小误判率高）
    truncation_min_input_lines: int = 20
    # 全局 LLM API 并发上限（跨所有 pipeline 共享的 asyncio.Semaphore 名额）
    max_concurrent_requests: int = 3
    # 精修结果磁盘缓存：写到 {output_dir}/.llm_cache/；同 input+model+prompt
    # 指纹的段自动命中，resume 任务可跳过已精修段。只缓存非截断的成功结果。
    enable_cache: bool = True


class OutputConfig(BaseModel):
    """输出配置"""

    image_format: str = "jpg"
    image_quality: int = 95


class CustomWord(BaseModel):
    """自定义敏感词条目。

    code 非空时用它作为该词的替换；为空时回退到 PIIConfig.custom_words_placeholder。
    frozen 保证可 hash，便于去重与集合操作。
    """

    model_config = ConfigDict(frozen=True)

    word: str
    code: str = ""


class PIIConfig(BaseModel):
    """PII 脱敏配置"""

    enable: bool = False  # 默认关闭，按需启用 PII 脱敏

    # 结构化 PII（regex）
    redact_phone: bool = True
    redact_email: bool = True
    redact_id_card: bool = True
    redact_bank_card: bool = True

    # 实体 PII（LLM）
    redact_person_name: bool = True
    redact_org_name: bool = True

    # 占位符
    phone_placeholder: str = "[手机号]"
    email_placeholder: str = "[邮箱]"
    id_card_placeholder: str = "[身份证号]"
    bank_card_placeholder: str = "[银行卡号]"
    person_name_placeholder: str = "[人名]"
    org_name_placeholder: str = "[机构名]"

    # 自定义敏感词（用户指定，每项可选代号）
    custom_sensitive_words: list[CustomWord] = Field(default_factory=list)
    custom_words_placeholder: str = "[敏感词]"

    # 实体检测失败时阻断云端调用（保证不外泄）
    block_cloud_on_detect_failure: bool = True


class PipelineConfig(BaseModel):
    """Pipeline 总配置"""

    ocr: OCRConfig = Field(default_factory=OCRConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    pii: PIIConfig = Field(default_factory=PIIConfig)
    db_path: str = "data/docrestore.db"  # SQLite 持久化路径
    debug: bool = True  # 落盘各阶段中间结果到 output_dir/debug/

    # 性能调试开关：开启后 Pipeline 全流程埋点，任务结束写 profile.json
    # + 打印扁平化耗时表。默认关闭以避免生产环境引入 ~1-2μs/stage 开销。
    # 环境变量 DOCRESTORE_PROFILING=1 可强制覆盖。
    profiling_enable: bool = False
    # profile.json 输出路径；空串 → {output_dir}/profile.json
    profiling_output_path: str = ""
