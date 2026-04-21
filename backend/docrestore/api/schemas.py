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

from pydantic import BaseModel


class LLMConfigRequest(BaseModel):
    """LLM 配置（请求级覆盖）"""

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    max_chars_per_segment: int | None = None


class OCRConfigRequest(BaseModel):
    """OCR 配置（请求级覆盖）"""

    model: str | None = None
    gpu_id: str | None = None  # GPU 选择（CUDA_VISIBLE_DEVICES）
    paddle_python: str | None = None
    paddle_ocr_timeout: int | None = None
    paddle_server_url: str | None = None
    paddle_server_model_name: str | None = None


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


class CreateTaskRequest(BaseModel):
    """创建任务请求"""

    image_dir: str
    output_dir: str | None = None
    llm: LLMConfigRequest | None = None
    ocr: OCRConfigRequest | None = None
    pii: PIIConfigRequest | None = None


class UpdateMarkdownRequest(BaseModel):
    """更新文档 Markdown 内容"""

    markdown: str


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
