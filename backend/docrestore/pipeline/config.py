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

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# PaddleOCR-VL genai_server 默认 backend_config（含 enforce_eager）。12G 显存卡
# 启动 VL-1.6 若不关 CUDA graph，vLLM 会报 "No available memory for the cache
# blocks" 而失败；详见该 YAML。设为空串则不传 --backend_config（≥16G 显存可用，
# 换取 CUDA graph 提速）。
_PPOCR_VL_BACKEND_CONFIG = str(
    Path(__file__).resolve().parent.parent
    / "resources"
    / "ppocr_vl_backend.yaml"
)


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
    #: 本次任务排除的输入图（相对 image_dir 的路径，与裁剪框 key 同空间）。
    #: 前端"正文裁剪"面板删除图片时填；仅任务级生效（扫描时跳过），
    #: 绝不删除 / 移动磁盘上的源文件。随任务配置持久化，resume 自动沿用。
    exclude_images: list[str] = Field(default_factory=list)
    #: 用户手动确认的正文裁剪框（相对 image_dir 路径 → (x0,y0,x1,y1)）。
    #: 任务级生效：OCR 前按框裁到任务输出目录（crop_page_manual），优先于
    #: 自动检测；**绝不写用户目录**——旧版就地覆盖原图在只读挂载上静默失败、
    #: 可写时毁原图，已废弃。随任务配置持久化，resume 自动沿用。
    crop_boxes: dict[str, tuple[int, int, int, int]] = Field(
        default_factory=dict,
    )
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
    # gpu_detect.pick_best_gpu() 选 OCR 默认卡，保持跨机器可移植。
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
    #: PaddleOCR pipeline 选择
    #: - ``vl``：PaddleOCR-VL（vllm-server 模式），文档场景默认；输出 markdown
    #:   + 块级 layout（``parsing_res_list``），需先拉 ppocr-server 进程
    #: - ``basic``：PP-OCRv5（DBNet+CRNN），AGE-8 IDE 代码场景；输出**行级**
    #:   ``rec_boxes``+text+score（填充到 ``PageOCR.text_lines``），不需要
    #:   vllm-server，纯本地推理
    paddle_pipeline: Literal["basic", "vl"] = "vl"
    paddle_python: str = ""  # PaddleOCR conda 环境的 python 路径
    paddle_ocr_timeout: int = 300  # 单张 OCR 超时（秒）
    paddle_restart_interval: int = 20  # 每 N 张图片重启 worker（0 禁用）
    # worker 脚本路径（空串时使用默认仓库内路径）
    paddle_worker_script: str = ""
    # ppocr-server 的 --backend_config YAML 路径（默认指向仓库内 enforce_eager
    # 配置，避免 12G 卡 VL-1.6 OOM；空串则用 PaddleOCR 默认，仅 ≥16G 显存推荐）
    paddle_server_backend_config: str = _PPOCR_VL_BACKEND_CONFIG

    # PaddleOCR server 模式（paddle_server_url 非空时启用）
    paddle_server_url: str = ""  # 如 "http://localhost:8119/v1"
    paddle_server_model_name: str = "PaddleOCR-VL-1.6-0.9B"
    #: PaddleOCR-VL 管线版本（``v1`` / ``v1.5`` / ``v1.6``）。决定版面模型
    #: （PP-DocLayout）与 VL prompt 版本，需与 ``paddle_server_model_name`` 的
    #: 模型版本匹配（如 1.6 模型 + ``v1.6``）。VL-1.5 已废弃，默认 ``v1.6``。
    paddle_pipeline_version: str = "v1.6"
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
    #: 基础超时（秒）。实际 timeout 按 input_chars 线性放大，见 effective_timeout。
    #: 默认 60s —— 正常 API p95 远低于此值；超时代表服务端真的挂了，快速失败
    #: + litellm num_retries 自动重试比长等实用。历史上设 600s（10 分钟），
    #: gpt-5.4-nano profile 出现过 904s 单次挂起 —— 那是 timeout=600 触发一次
    #: retry 凑出来的。改小后类似 outlier 在 180s 内就被切断。
    timeout: int = 60
    #: 每 1000 个 input 字符额外给多少秒。大段 LLM 本身就慢，要线性放宽。
    timeout_per_1k_chars_s: float = 3.0
    #: 单次 timeout 上限（秒）。防止超长 input 把 timeout 放到天上去。
    timeout_max_s: int = 180
    #: 统一 LLM 精修总开关：文档（分段）/ 代码 / PPT（按页）三模式共用。
    #: True=按各模式策略精修；False=跳过所有 LLM 精修，仅输出 OCR 清洗结果。
    #: 在 ``_get_refiner`` 单点拦截——False 时返回 None，下游各模式既有的
    #: ``if refiner is None: 跳过`` 回退路径统一生效（无需逐处判断）。
    enable_refine: bool = True
    enable_final_refine: bool = True  # 分段精修后是否做整篇文档级精修
    # 整篇精修分块：文档超过 final_refine_min_chars 时切成 final_refine_chunks 块
    # 并行调用；块数 ≤1 退化为单次整篇调用。每块按 <!-- page: --> 边界切分。
    final_refine_chunks: int = 3
    final_refine_min_chars: int = 20000
    # OpenAI Predicted Outputs (prediction 参数)：对 refine/final_refine 这类
    # 输出 ≈ 输入的改写任务可提速 2-4×。仅 gpt-4o 系支持，gpt-5 全系不支持。
    # 默认关闭；切换到支持模型时置 True 即可生效。
    enable_prediction: bool = False
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
    #: 代码模式 LLM 修正策略：
    #:   - "refine"（默认）：字符级修正，**严格保持行数**，安全但部分
    #:     OCR 损伤（如整行 `}` 错识为多字符）修不动
    #:   - "rewrite"：允许 LLM 重新排版/合并断行/补编译必需的语法元素，
    #:     不强制行数守恒；适合长文件 OCR 损伤密集的场景，但需要更强模型
    code_refine_mode: str = "refine"


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
    #: 凭据 / token（label 锚定的 password=/token=/账号: 键值对 + URL 内联
    #: user:pass@ + sk-/ghp_/AKIA/JWT 已知格式）。在 producer 入队前的正则层执行
    #: → 上云端精修与落盘前就抹掉，覆盖正则原本兜不到的密码/用户名/账号/token。
    #: 偏向"宁多勿漏"（over-redact 安全），技术文档误报高时可关此项。
    redact_credential: bool = True
    #: user@host 连接目标（scp/ssh/rsync 目标，user 常含人名）。user@IP 与
    #: user@主机名都脱；邮箱步骤先吃掉带 TLD 的 user@domain.tld。宁多勿漏，可关。
    redact_host: bool = True
    #: 内部 URL：私有内网 IP（10/172.16-31/192.168/127）的 URL 一律脱；host 命中
    #: sensitive_url_domains 后缀的也脱。非私有 / 非配置域名的公网链接不动。
    redact_internal_url: bool = True

    # 实体 PII（LLM）
    redact_person_name: bool = True
    redact_org_name: bool = True

    # 占位符
    phone_placeholder: str = "[手机号]"
    email_placeholder: str = "[邮箱]"
    id_card_placeholder: str = "[身份证号]"
    bank_card_placeholder: str = "[银行卡号]"
    credential_placeholder: str = "[凭据]"
    host_placeholder: str = "[主机地址]"
    internal_url_placeholder: str = "[内部链接]"
    person_name_placeholder: str = "[人名]"
    org_name_placeholder: str = "[机构名]"

    # 自定义敏感词（用户指定，每项可选代号）
    custom_sensitive_words: list[CustomWord] = Field(default_factory=list)
    custom_words_placeholder: str = "[敏感词]"

    #: 内部 URL 敏感域名后缀（如 ``antfin.com``）。host 等于或为其子域的 URL
    #: 整体脱成 internal_url_placeholder（大小写无关）。空列表时仅私有 IP 的 URL
    #: 被脱。配置后即覆盖如 ``aliyuque.antfin.com/...`` 这类公网内部平台链接。
    sensitive_url_domains: list[str] = Field(default_factory=list)

    # 实体检测失败时阻断云端调用（保证不外泄）
    block_cloud_on_detect_failure: bool = True

    # 本地 NER（人名/机构名检测，上云前脱敏，pii-local-ner.md）
    #: 本地 NER 后端。"spacy"=spaCy CNN 模型（唯一实现）；"none"=显式关闭本地实体
    #: 检测（结构化正则仍跑、不阻断云端，属知情放弃）。GLiNER 已弃用（transformers
    #: 撞 OCR venv，破坏环境），不设取值。
    ner_backend: Literal["spacy", "none"] = "spacy"
    #: 本地 NER 模型集（spaCy 模型名）。默认中英双模覆盖中文文档 + 代码英文名。≥1 个
    #: 加载成功即"可用"（缺的告警跳过，属召回边界）；全缺则 fail-closed。必须用 CNN
    #: 模型（*_md/_sm/_lg），禁用 *_trf（依赖 transformers，撞 OCR venv）。
    ner_models: list[str] = Field(
        default_factory=lambda: ["zh_core_web_md", "en_core_web_md"],
    )


class CodeRestoreConfig(BaseModel):
    """AGE-8 IDE 代码照片 → 源文件还原配置

    enable=True 时启用 IDE 代码场景，pipeline 自动切换：
      - OCR 切到 ``basic`` pipeline（PP-OCRv5 行级 bbox）
      - 走行号列锚点 + 栏代码组装的 IDE 专用流程
    """

    enable: bool = False
    #: 输出源文件子目录名（output_dir/<files_dir>/<relative-path>）
    output_files_dir: str = "files"
    #: 跨张归类策略：tab_breadcrumb（用 tab+breadcrumb 路径分组，AGE-46
    #: 默认）/ content_only（仅按代码内容连续性分组，未实现）
    file_grouping_strategy: Literal["tab_breadcrumb", "content_only"] = (
        "tab_breadcrumb"
    )
    #: 是否在首轮整图 OCR 找到 IDE column 后，对每个代码 column 裁剪增强并
    #: 重跑 OCR。默认关闭，避免对不支持临时图片 OCR 的测试/轻量环境增加成本。
    secondary_column_ocr: bool = False
    secondary_column_ocr_scale: int = 2
    secondary_column_ocr_padding_px: int = 6
    secondary_column_ocr_contrast: float = 1.35
    secondary_column_ocr_sharpness: float = 1.4
    #: 可选参考源码根目录。默认空字符串表示关闭；只读离线检索，不联网。
    context_root: str = ""

    # --- AGE-80 Stage 1 批量文件名/路径归一阈值 ---
    #: full-path 加权支持度（Σ path_confidence）≥ 此值进权威词表。
    vocab_support_threshold: float = 1.5
    #: full-path 出现频次 ≥ 此值进权威词表（与 support 取或）。
    vocab_min_frequency: int = 3
    #: snap 时 filename stem 编辑距离上限（同扩展名前提下）。取 1（保守）：
    #: 实测距离 2 会误并真实近名文件（如 x11 vs x11xv，差 "xv" 两字符），
    #: 距离 2 的歧义交 S2 行号内容裁决。
    snap_filename_max_distance: int = 1
    #: snap 时 compact dir 编辑距离上限（容忍漏字符 / 虚假单字符目录段）。
    snap_dir_max_distance: int = 2
    #: 仅当碎片支持度 ≤ 此比例 × 目标支持度才 snap（只并少数派噪声，不合并
    #: 两个体量相当的同名近邻文件——那种交由 S2 行号内容裁决）。
    snap_minority_ratio: float = 0.5

    # --- AGE-81 Stage 2 行号锚定归类阈值 ---
    #: 重合区有效（双方可信）行数下限，低于则视为无可用重合。
    overlap_min_lines: int = 3
    #: 重合区内容一致率 ≥ 此值 → 确认续接 / 跨桶救援命中。
    overlap_confirm_ratio: float = 0.90
    #: 重合区内容一致率 ≤ 此值 → 判内容冲突。
    overlap_conflict_ratio: float = 0.50
    #: 页数 ≤ 此值的小组才作为跨桶救援的 orphan 候选。
    rescue_max_orphan_pages: int = 3


class PowerPointRestoreConfig(BaseModel):
    """PPT 屏摄照片还原模式配置（AGE-83）。

    enable=True 时启用第三分支 ``_ppt_pipeline``：屏摄照片 → S2 透视矫正
    （逐页前处理）→ VL-1.6 doc_parser 识别 + 化学结构裁图 → 单页保序组装
    合并为单个 document.md。与文档 / 代码模式互斥三选一。
    """

    enable: bool = False
    #: S2 透视矫正（默认开，S0 结论：屏摄强透视下矫正必需）
    rectify: bool = True
    #: 落盘 before/after 对照图到 output_dir/<rectify_debug_dir>（S2 验收证据）
    rectify_save_debug: bool = True
    rectify_debug_dir: str = ".rectified"
    #: 顶边上抬比例，补回常被吊顶 / 暗标题栏遮挡的区域
    rectify_top_extend_ratio: float = 0.2


class PdfRenderConfig(BaseModel):
    """PDF 输入逐页渲染配置（Epic A）。

    上传 / 直传的 PDF 在 pipeline 摄取入口逐页渲染成 PNG 后，复用既有图片
    OCR → 去重 → 精修链路；一个 PDF = 一篇文档。本阶段仅用服务端默认，不暴露
    请求级覆盖、不进 DB。详见 ``docs/zh/pdf-mode.md``。
    """

    #: 是否渲染 PDF 输入（关闭则 .pdf 被 scan_images 忽略、不产文档）
    enable: bool = True
    #: 渲染分辨率（DPI 200 已验证与 PaddleOCR-VL 同像素契约）
    dpi: int = 200
    #: 单 PDF 页数硬上限，超出截断前 N 页 + warning（防内存 / 磁盘打爆）
    max_pages: int = 500
    #: 渲染 PNG 长边上限，超出按比例降采样（防超大幅面页撑爆 OCR）
    max_long_side: int = 4096
    #: 页号零填充位数，保证 scan_images 字典序 = 页序
    zero_pad: int = 4


class ContentCropConfig(BaseModel):
    """文档模式正文区裁剪配置（仅文档模式生效）。

    屏摄文档照片含左导航 / 右大纲 / 顶部 UI；文档模式无行号锚定，这些会污染正文 OCR。
    enable=True 时在 OCR 前自动检测正文主列、裁掉左右侧栏；已裁剪 / 无侧栏图自动跳过
    （恒等放行），对历史已人工裁剪的图无害。详见 ``docs/zh/doc-content-crop.md``。
    """

    enable: bool = True
    #: 落盘裁剪图与检测框对照到 output_dir/<debug_dir>（验收证据 + 前端预览源）。
    save_debug: bool = True
    debug_dir: str = ".content_crop"


class PipelineConfig(BaseModel):
    """Pipeline 总配置"""

    ocr: OCRConfig = Field(default_factory=OCRConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    pii: PIIConfig = Field(default_factory=PIIConfig)
    code: CodeRestoreConfig = Field(default_factory=CodeRestoreConfig)
    ppt: PowerPointRestoreConfig = Field(default_factory=PowerPointRestoreConfig)
    content_crop: ContentCropConfig = Field(default_factory=ContentCropConfig)
    pdf: PdfRenderConfig = Field(default_factory=PdfRenderConfig)
    db_path: str = "data/docrestore.db"  # SQLite 持久化路径
    debug: bool = True  # 落盘各阶段中间结果到 output_dir/debug/

    # 性能调试开关：开启后 Pipeline 全流程埋点，任务结束写 profile.json
    # + 打印扁平化耗时表。默认关闭以避免生产环境引入 ~1-2μs/stage 开销。
    # 环境变量 DOCRESTORE_PROFILING=1 可强制覆盖。
    profiling_enable: bool = False
    # profile.json 输出路径；空串 → {output_dir}/profile.json
    profiling_output_path: str = ""
