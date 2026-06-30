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

"""API 请求/响应 pydantic 模型"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LLMConfigRequest(BaseModel):
    """LLM 配置（请求级覆盖）"""

    #: provider 选择：``cloud`` 走云端 API（litellm 默认路径，含 PII 实体识别），
    #: ``local`` 走本地 OpenAI 兼容服务（vLLM / ollama / llama.cpp 等，
    #: 数据不出本地、跳过 LLM 实体识别只走 regex 脱敏）。
    #: 用 Literal 严格校验，非法值（拼写错误等）直接 422 拒绝。
    provider: Literal["cloud", "local"] | None = None
    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    max_chars_per_segment: int | None = None
    #: 代码模式 LLM 修正策略：``refine``（行数守恒，安全） /
    #: ``rewrite``（允许重排 + 补语法，激进，需更强模型）
    code_refine_mode: str | None = None
    #: 统一 LLM 精修总开关：文档（分段）/ 代码 / PPT（按页）三模式共用。
    #: None=不覆盖（用后端默认 True）；False=本任务跳过所有 LLM 精修。
    enable_refine: bool | None = None


class OCRConfigRequest(BaseModel):
    """OCR 配置（请求级覆盖）

    安全约束（#32 / #33）：**基础设施字段绝不在此暴露**。
    ``paddle_python`` / ``paddle_server_python`` / ``*_worker_script``
    可控即任意本地二进制执行（RCE）；``paddle_server_url`` /
    ``paddle_server_model_name`` 可控即把页面图 POST 到攻击者/内网地址
    （SSRF + 数据外泄）。这些一律只由服务端配置注入，不接受请求级覆盖。
    routes 合成时另有 ``_OCR_SAFE_OVERRIDE_ALLOW`` allowlist 做 sink 兜底
    （默认拒绝，只放行登记过的业务字段）。新增字段前先判断是否基础设施级——
    是则不要加在这里；若是安全业务字段，记得同步登记到该 allowlist，
    否则其请求级覆盖会被静默丢弃。
    """

    model: str | None = None
    gpu_id: str | None = None  # GPU 选择（CUDA_VISIBLE_DEVICES）
    #: 任务级排除的输入图（相对 image_dir，与 crop_boxes key 同空间）
    exclude_images: list[str] | None = None
    paddle_pipeline: Literal["basic", "vl"] | None = None
    paddle_ocr_timeout: int | None = None


class CustomSensitiveWord(BaseModel):
    """自定义敏感词条目（word + 可选 code）"""

    word: str
    code: str | None = None


class PIIConfigRequest(BaseModel):
    """PII 脱敏配置（请求级覆盖）

    custom_sensitive_words 支持两种写法以便前端平滑迁移：
    - `["张三", "某公司"]`：纯字符串列表（使用默认占位符）
    - `[{"word": "张三", "code": "化名A"}, {"word": "某公司"}]`：对象列表（可选 code）
    """

    enable: bool | None = None
    custom_sensitive_words: (
        list[CustomSensitiveWord] | list[str] | None
    ) = None
    # NER 后端 opt-out（#64）：无 spaCy 环境想只做结构化（手机/邮箱/卡号）正则脱敏时，
    # 把 ner_backend 设 "none" 或关人名/机构名脱敏，避免 _guard_ner_backend 硬 400。
    # 不传（None）则沿用服务端默认（spacy + 人名/机构名脱敏开）。
    ner_backend: Literal["spacy", "none"] | None = None
    redact_person_name: bool | None = None
    redact_org_name: bool | None = None


class CodeRestoreConfigRequest(BaseModel):
    """AGE-8 IDE 代码模式配置（请求级覆盖）"""

    enable: bool | None = None
    output_files_dir: str | None = None
    secondary_column_ocr: bool | None = None
    secondary_column_ocr_scale: int | None = None
    secondary_column_ocr_padding_px: int | None = None
    secondary_column_ocr_contrast: float | None = None
    secondary_column_ocr_sharpness: float | None = None
    context_root: str | None = None


class PowerPointRestoreConfigRequest(BaseModel):
    """PPT 屏摄还原模式配置（请求级覆盖，全可选；None = 用后端默认）"""

    enable: bool | None = None
    rectify: bool | None = None
    rectify_save_debug: bool | None = None


class CropBox(BaseModel):
    """正文裁剪框（像素坐标，原图坐标系；左上 (x0,y0) 右下 (x1,y1)）。"""

    x0: int
    y0: int
    x1: int
    y1: int


class CropPoint(BaseModel):
    """单个角点（原图像素坐标系）。"""

    x: int
    y: int


class CropQuad(BaseModel):
    """四角点（原图像素坐标系），顺序即角色：左上 / 右上 / 右下 / 左下。

    供编辑模式"四角校正"：用户放 4 个角点框住倾斜 / 透视变形的插图，后端按此
    顺序透视变换矫正为正视矩形（顺序由前端固定角色手柄保证，后端不重排）。
    """

    tl: CropPoint
    tr: CropPoint
    br: CropPoint
    bl: CropPoint


class CropDetectRequest(BaseModel):
    """裁剪框检测请求：对 image_dir 下每张图给建议正文区框。"""

    image_dir: str


class CropDetectItem(BaseModel):
    """单张图检测结果。box=None 表示无需裁剪（已裁剪 / 无侧栏 / 检测失败）。"""

    name: str
    width: int
    height: int
    box: CropBox | None = None


class CropDetectResponse(BaseModel):
    """裁剪框检测响应。"""

    images: list[CropDetectItem]


class CropFigureRequest(BaseModel):
    """编辑模式手动重截插图请求：从某张源图裁一块，存进文档 images/。

    ``box`` / ``quad`` 二选一：``quad`` 优先（四角透视校正），否则用 ``box``
    （矩形裁剪）；两者皆空由路由报 400。
    """

    #: 源图相对名（相对 task.image_dir，来自 source-images 列表）。
    source_filename: str
    #: 矩形裁剪框（源图像素坐标系）；与 quad 二选一。
    box: CropBox | None = None
    #: 四角校正点（源图像素坐标系）；提供时优先透视矫正，与 box 二选一。
    quad: CropQuad | None = None
    #: 多文档时目标文档子目录（存进 output_dir/{doc_dir}/images/）；
    #: 单文档省略或空串 = 根 output_dir/images/。
    doc_dir: str | None = None


class CropFigureResponse(BaseModel):
    """重截插图响应：asset_path 为 markdown 相对引用（images/xxx.jpg）。"""

    asset_path: str


class CreateTaskRequest(BaseModel):
    """创建任务请求"""

    image_dir: str
    output_dir: str | None = None
    llm: LLMConfigRequest | None = None
    ocr: OCRConfigRequest | None = None
    pii: PIIConfigRequest | None = None
    code: CodeRestoreConfigRequest | None = None
    ppt: PowerPointRestoreConfigRequest | None = None
    #: 正文裁剪框（图名 → 框，原图坐标系）。提供时创建任务前按框预裁剪图片再跑，
    #: content_crop 的已裁剪判据会自动跳过、不二次裁。None=不预裁、走自动检测。
    crop_boxes: dict[str, CropBox] | None = None


class UpdateMarkdownRequest(BaseModel):
    """更新文档 Markdown 内容"""

    markdown: str


class UpdateCodeFileRequest(BaseModel):
    """更新代码模式源文件内容"""

    content: str


class DiagnoseCodeFileRequest(BaseModel):
    """诊断代码模式源文件草稿内容"""

    file_path: str
    content: str


class CodeDiagnosticItemResponse(BaseModel):
    """代码诊断单条行级标注"""

    line: int
    column: int = 0
    severity: str = "error"
    category: str = "syntax"
    code: str = ""
    message: str = ""
    source: str = ""


class CodeDiagnosticResponse(BaseModel):
    """代码诊断响应"""

    path: str
    language: str
    status: str
    category: str
    summary: str = ""
    failing_lines: list[int] = Field(default_factory=list)
    syntax_errors: int = 0
    semantic_errors: int = 0
    dependency_errors: int = 0
    items: list[CodeDiagnosticItemResponse] = Field(default_factory=list)
    tool: str = ""
    duration_ms: int = 0


class ProgressResponse(BaseModel):
    """进度信息"""

    stage: str
    current: int
    total: int
    percent: float
    message: str
    subtask: str = ""  # 子目录标识（非空=process_tree 并行的某一路）


class TaskResponse(BaseModel):
    """任务状态响应"""

    task_id: str
    status: str
    progress: ProgressResponse | None = None
    error: str | None = None
    # 供前端进度区按任务调整展示：是否启用 LLM 精修 + 处理模式（doc/code/ppt）。
    # 关精修时文档模式隐藏「LLM 精修」轨、PPT/代码模式该轨改名「后处理」。
    # 默认值兼容旧前端/创建响应（仅含 task_id+status 时取默认）。
    enable_refine: bool = True
    mode: str = "doc"


class TaskResultResponse(BaseModel):
    """任务结果响应（单篇文档）

    error 非空时表示该子文档处理失败：markdown 可能为空或残缺，前端应显示
    错误文本而非 markdown 预览。成功子文档 error=""。
    """

    task_id: str
    output_path: str
    markdown: str
    doc_title: str = ""
    doc_dir: str = ""
    error: str = ""
    #: 软降级警告（#96，非致命）：VL 退本地推理 / PDF 缺页 / 段截断等。error
    #: 为空但 warnings 非空 = 文档可用但有降级，前端显示软提示而非失败态。
    warnings: list[str] = Field(default_factory=list)


class TaskResultsResponse(BaseModel):
    """任务结果响应（多篇文档列表）"""

    task_id: str
    results: list[TaskResultResponse]


# ── 任务列表 ──────────────────────────────────────────


class ActionResponse(BaseModel):
    """通用操作响应"""

    task_id: str
    message: str = ""


class TaskListItem(BaseModel):
    """任务列表中的单项"""

    task_id: str
    status: str
    image_dir: str
    output_dir: str
    error: str | None = None
    created_at: str
    result_count: int = 0


class TaskListResponse(BaseModel):
    """任务列表响应（分页）"""

    tasks: list[TaskListItem]
    total: int
    page: int
    page_size: int


class TaskCleanupRequest(BaseModel):
    """批量清理任务请求。

    出于安全考虑，仅允许清理终态任务（completed / failed），禁止传入
    pending / processing，避免误删运行中的任务。
    """

    statuses: list[str]


class TaskCleanupResponse(BaseModel):
    """批量清理任务响应"""

    deleted: int = 0
    failed: int = 0
    deleted_ids: list[str] = []
    errors: list[str] = []


# ── 文件上传 ──────────────────────────────────────────


class DirEntry(BaseModel):
    """目录/文件条目。

    is_dir=True 表示目录（可选携带 image_count：顶层图片数）；
    is_dir=False 时额外携带 size_bytes（文件大小）。
    """

    name: str
    is_dir: bool
    size_bytes: int | None = None
    image_count: int | None = None


class BrowseDirsResponse(BaseModel):
    """目录浏览响应。

    entries 同时包含子目录和文件（文件仅在 include_files=True 时返回）。
    """

    path: str
    parent: str | None = None
    entries: list[DirEntry]


class StageServerSourceRequest(BaseModel):
    """将服务器上已有文件 stage 为 image_dir 的请求。

    paths 中的每一项必须是绝对路径、指向存在的普通文件。
    服务端会创建临时目录并以符号链接指向这些文件，返回 image_dir。
    """

    paths: list[str]


class StageServerSourceResponse(BaseModel):
    """服务器源 stage 响应"""

    image_dir: str
    file_count: int


class SourceImagesResponse(BaseModel):
    """源图片列表响应"""

    task_id: str
    images: list[str]


class LayoutBlockPayload(BaseModel):
    """版面块：bbox + 类型 + raw 文字 / 图片引用（前端匹配光标块，Epic E）。"""

    bbox: tuple[int, int, int, int]  # (x0, y0, x1, y1) 像素
    label: str
    text: str
    #: 图片 / 图表块的输出引用 ``images/{stem}_N.ext``（对齐 markdown <img src>），
    #: 供前端按引用匹配光标所在图片块；文字块为空。
    image_ref: str = ""


class LayoutPagePayload(BaseModel):
    """单页版面：原图文件名 + 像素尺寸（bbox 坐标空间）+ 块列表。"""

    filename: str
    image_size: tuple[int, int]  # (width, height) 像素，作 % 换算分母
    blocks: list[LayoutBlockPayload]


class LayoutPayload(BaseModel):
    """任务版面高亮载荷（Epic E）：各页块 bbox，供编辑器光标↔原图高亮。

    ``processed``：bbox/image_size 是否处于**处理图**坐标系——OCR 前做过预处理
    （PPT 透视矫正 `_after` / 文档 content_crop 正文裁剪 `_crop` / 手动裁剪），坐标空间
    与原图不符（§13/§15）。为真时前端源图栏须改显对应处理图才对齐（逐页尝试，
    无处理图的页 onError 回退原图）；无预处理时为假（bbox=原图坐标，显原图）。
    """

    pages: list[LayoutPagePayload]
    processed: bool = False


class CodeLineBoxPayload(BaseModel):
    """代码单行在原图的像素框（#93 悬停放大）：行号 + 来源页 + bbox。"""

    line_no: int  # OCR 行号（与前端编辑器 data-line 同值）
    page: str  # 来源页标识 ``{page_stem}.col{column_index}``
    bbox: tuple[int, int, int, int]  # (x0, y0, x1, y1) 原图像素


class CodeFileLayoutPayload(BaseModel):
    """单个源文件的行级版面：path 对齐 files-index entry.path。"""

    path: str
    lines: list[CodeLineBoxPayload]


class CodeLayoutPayload(BaseModel):
    """代码任务行级版面载荷（#93）：各源文件逐行 bbox，供悬停行↔原图局部放大。

    代码模式无 content_crop / 无矫正 → bbox 恒在原图坐标系，故无 ``processed`` 字段
    （前端加载原图读 naturalWidth/Height 即可）。
    """

    files: list[CodeFileLayoutPayload]


class UploadSessionResponse(BaseModel):
    """创建上传会话响应"""

    session_id: str
    max_file_size_mb: int
    allowed_extensions: list[str]


class UploadFilesResponse(BaseModel):
    """上传文件响应"""

    session_id: str
    uploaded: list[str]
    total_uploaded: int
    failed: list[str]


class UploadFileItem(BaseModel):
    """上传会话中的单个文件条目"""

    session_id: str
    file_id: str
    filename: str
    relative_path: str
    size_bytes: int
    created_at: str


class UploadSessionFilesResponse(BaseModel):
    """上传会话文件列表响应"""

    session_id: str
    files: list[UploadFileItem]


class UploadSessionFileDeleteResponse(BaseModel):
    """上传会话单文件删除响应"""

    session_id: str
    file_id: str
    remaining_count: int


class UploadCompleteResponse(BaseModel):
    """完成上传响应"""

    session_id: str
    image_dir: str
    file_count: int
    total_size_bytes: int


# ── OCR 引擎预热 ──────────────────────────────────────────


class OCRWarmupRequest(BaseModel):
    """OCR 引擎预热请求

    gpu_id 为 None 时由后端自动探测（`gpu_detect.pick_best_gpu`）。
    """

    model: str = "paddle-ocr/ppocr-v4"
    gpu_id: str | None = None


class OCRStatusResponse(BaseModel):
    """OCR 引擎状态响应"""

    current_model: str
    current_gpu: str
    current_gpu_name: str = ""  # 人类可读型号，便于 UI 区分同机多卡
    is_ready: bool
    is_switching: bool
    #: 降级原因码（#96，空=未降级）：请求 VL 但退回本地推理时为
    #: vl_no_server_python / vl_server_python_missing，前端徽章据此提示。
    degraded_reason: str = ""


# ── 本地 NER 可用性 ───────────────────────────────────────


class NERStatusResponse(BaseModel):
    """本地 NER 可用性探测响应（GET /ner/status，不加载模型）。

    前端在开启 PII + 人名/机构名脱敏时拉取；``available=False`` 时弹「一键配置本地
    NER 环境」入口（POST /ner/setup）。
    """

    available: bool  # spaCy 已装 且 ≥1 个配置模型就绪
    spacy_installed: bool
    configured_models: list[str]
    installed_models: list[str]
    missing_models: list[str]


class NERSetupStatusResponse(BaseModel):
    """本地 NER 环境安装状态（POST /ner/setup 受理 + GET /ner/setup/status 轮询）。"""

    state: Literal["idle", "running", "done", "failed"]
    log: list[str]  # 安装日志尾部（pip / spacy download 输出）
    error: str  # 失败时的错误摘要，成功/进行中为空串


# ── GPU 列表 ──────────────────────────────────────────────


class GPUInfoResponse(BaseModel):
    """单张 GPU 的可展示信息（透传 gpu_detect.GPUInfo）。"""

    index: str
    name: str
    memory_total_mb: int
    memory_free_mb: int | None = None
    compute_capability: str | None = None


class GPUListResponse(BaseModel):
    """GET /gpus 响应：GPU 列表 + 推荐索引。"""

    gpus: list[GPUInfoResponse]
    recommended: str | None = None
