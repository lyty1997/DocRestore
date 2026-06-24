/**
 * API 客户端：封装 fetch 调用 + zod 校验
 */

import {
  ActionResponseSchema,
  BrowseDirsResponseSchema,
  CreateTaskResponseSchema,
  SourceImagesResponseSchema,
  StageServerSourceResponseSchema,
  TaskCleanupResponseSchema,
  TaskListResponseSchema,
  TaskResponseSchema,
  TaskResultResponseSchema,
  TaskResultsResponseSchema,
  FilesIndexSchema,
  DiagnoseCodeFileResponseSchema,
  UploadCompleteResponseSchema,
  UploadFilesResponseSchema,
  UploadSessionFileDeleteResponseSchema,
  UploadSessionFilesResponseSchema,
  UploadSessionResponseSchema,
  OcrStatusResponseSchema,
  OcrWarmupResponseSchema,
  NerStatusResponseSchema,
  NerSetupStatusResponseSchema,
  GpuListResponseSchema,
  CropDetectResponseSchema,
  CropFigureResponseSchema,
  LayoutPayloadSchema,
  type LayoutPayload,
  type ActionResponse,
  type BrowseDirsResponse,
  type CreateTaskResponse,
  type SourceImagesResponse,
  type StageServerSourceResponse,
  type TaskCleanupResponse,
  type TaskListResponse,
  type TaskResponse,
  type TaskResultResponse,
  type TaskResultsResponse,
  type FilesIndex,
  type DiagnoseCodeFileResponse,
  type UploadCompleteResponse,
  type UploadFilesResponse,
  type UploadSessionFileDeleteResponse,
  type UploadSessionFilesResponse,
  type UploadSessionResponse,
  type OcrStatusResponse,
  type OcrWarmupResponse,
  type NerStatusResponse,
  type NerSetupStatusResponse,
  type GpuListResponse,
  type CropBox,
  type CropQuad,
  type CropDetectResponse,
  type CropFigureResponse,
} from "./schemas";
import { appendTokenToUrl, getAuthHeaders, loadApiToken } from "./auth";

/** API 基础路径（开发环境通过 Vite proxy 转发） */
const API_BASE = "/api/v1";

/** 创建任务请求体 */
interface CreateTaskBody {
  image_dir: string;
  output_dir?: string | undefined;
  llm?: {
    /** 云端 / 本地 provider（#49 显式契约，与后端 LLMConfig.provider 对齐） */
    provider?: "cloud" | "local" | undefined;
    model?: string | undefined;
    api_base?: string | undefined;
    api_key?: string | undefined;
    max_chars_per_segment?: number | undefined;
    /** 统一 LLM 精修总开关（文档/代码/PPT 共用）；省略=后端默认 true */
    enable_refine?: boolean | undefined;
  } | undefined;
  pii?: {
    enable?: boolean | undefined;
    custom_sensitive_words?:
      | readonly { word: string; code?: string | undefined }[]
      | undefined;
    /** NER opt-out（#64）：无 spaCy 环境只做结构化正则脱敏；省略=后端默认 */
    ner_backend?: "spacy" | "none" | undefined;
    redact_person_name?: boolean | undefined;
    redact_org_name?: boolean | undefined;
  } | undefined;
  ocr?: {
    model?: string | undefined;
    gpu_id?: string | undefined;
    paddle_pipeline?: "basic" | "vl" | undefined;
    /** 任务级排除的输入图（相对 image_dir，与 crop_boxes key 同空间） */
    exclude_images?: readonly string[] | undefined;
  } | undefined;
  code?: {
    enable: boolean;
    output_files_dir?: string | undefined;
  } | undefined;
  ppt?: {
    enable: boolean;
  } | undefined;
  /** 正文裁剪框（图名→框）：前端"裁剪预览+微调"确认后填，建任务前预裁剪 */
  crop_boxes?: Record<string, CropBox> | undefined;
}

/** 合并认证 header 与自定义 header */
function apiHeaders(extra?: Record<string, string>): Record<string, string> {
  return { ...getAuthHeaders(), ...extra };
}

/** API 错误分类：网络层未拿到响应 / HTTP 非 2xx / 响应解析失败 */
export type ApiErrorKind = "network" | "http" | "parse";

/** i18n 占位符的可序列化值（数字直显 / 字符串字面 / 数组拼接） */
export type ApiErrorParams = Record<string, string | number | readonly string[]>;

/** 统一 API 错误：携带后端 code/params + 前端 i18n key，UI 用 i18n 翻译。
 *
 * 主信息翻译优先级：
 * 1. ``code`` 非空 → ``errors.api.<code-lowercase>``，``params`` 为占位符
 * 2. ``code`` 为空（network/parse 等客户端错误）→ ``messageKey``
 * 3. ``messageKey`` 也无 → ``message``（中文 fallback，开发友好）
 *
 * ``message`` 字段保留中文 fallback 便于 console.error 调试。
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly httpStatus?: number;
  /** 后端 APIErrorCode（如 ``TASK_NOT_FOUND``）；网络/parse 错误为空 */
  readonly code?: string;
  /** 后端响应 params（路径 / 原因 / 上限值等占位符值） */
  readonly params: ApiErrorParams;
  /** 客户端兜底主信息 i18n key（仅在没有 ``code`` 时使用） */
  readonly messageKey?: string;
  readonly messageKeyParams?: ApiErrorParams;
  /** HTTP 状态码诊断 hint i18n key（如 ``errors.http.504``） */
  readonly hintKey?: string;

  constructor(
    message: string,
    init: {
      kind: ApiErrorKind;
      httpStatus?: number;
      code?: string;
      params?: ApiErrorParams;
      messageKey?: string;
      messageKeyParams?: ApiErrorParams;
      hintKey?: string;
      cause?: unknown;
    },
  ) {
    super(message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiError";
    this.kind = init.kind;
    if (init.httpStatus !== undefined) this.httpStatus = init.httpStatus;
    if (init.code !== undefined) this.code = init.code;
    this.params = init.params ?? {};
    if (init.messageKey !== undefined) this.messageKey = init.messageKey;
    if (init.messageKeyParams !== undefined) {
      this.messageKeyParams = init.messageKeyParams;
    }
    if (init.hintKey !== undefined) this.hintKey = init.hintKey;
  }
}

/** HTTP 状态码 → 客户端诊断 hint i18n key（不含主错误，只是补充提示）。 */
function hintKeyForStatus(status: number): string | undefined {
  if (status === 413) return "errors.http.413";
  if (status === 504) return "errors.http.504";
  if (status >= 500) return "errors.http.5xx";
  return undefined;
}

/** 解析后端业务异常响应体（``ApiBusinessError`` 处理器输出形态）。 */
function parseBusinessErrorBody(text: string): {
  code?: string;
  detail?: string;
  params: ApiErrorParams;
} {
  try {
    const data: unknown = JSON.parse(text);
    if (typeof data !== "object" || data === null) return { params: {} };
    const obj = data as Record<string, unknown>;
    const code = typeof obj.code === "string" ? obj.code : undefined;
    const detail = typeof obj.detail === "string" ? obj.detail : undefined;
    const rawParams =
      typeof obj.params === "object" && obj.params !== null ? obj.params : {};
    /* params 只接收 string | number | string[]，其余字段静默忽略 */
    const params: ApiErrorParams = {};
    for (const [k, v] of Object.entries(rawParams)) {
      if (typeof v === "string" || typeof v === "number") {
        params[k] = v;
      } else if (
        Array.isArray(v) &&
        v.every((item) => typeof item === "string")
      ) {
        params[k] = v as readonly string[];
      }
    }
    return {
      ...(code === undefined ? {} : { code }),
      ...(detail === undefined ? {} : { detail }),
      params,
    };
  } catch {
    return { params: {} };
  }
}

/** 统一错误处理 */
async function handleResponse<T>(
  response: Response,
  schema: { parse: (data: unknown) => T },
): Promise<T> {
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    const parsed = parseBusinessErrorBody(text);
    const fallback = parsed.detail ?? (text || response.statusText);
    const hintKey = hintKeyForStatus(response.status);
    throw new ApiError(
      `HTTP ${response.status.toString()}: ${fallback}`,
      {
        kind: "http",
        httpStatus: response.status,
        ...(parsed.code === undefined ? {} : { code: parsed.code }),
        params: parsed.params,
        ...(hintKey === undefined ? {} : { hintKey }),
      },
    );
  }
  let json: unknown;
  try {
    json = await response.json();
  } catch (error_: unknown) {
    throw new ApiError("响应解析失败：非合法 JSON", {
      kind: "parse",
      messageKey: "errors.client.parseFailed",
      hintKey: "errors.client.parseFailedHint",
      cause: error_,
    });
  }
  return schema.parse(json);
}

/** 创建任务 */
export async function createTask(
  body: CreateTaskBody,
): Promise<CreateTaskResponse> {
  const response = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return handleResponse(response, CreateTaskResponseSchema);
}

/** 裁剪预览取图 URL（带认证 token）：从 image_dir 按相对名取一张图 */
export function getCropImageUrl(imageDir: string, name: string): string {
  return appendTokenToUrl(
    `${API_BASE}/crop/image?image_dir=${encodeURIComponent(imageDir)}` +
      `&name=${encodeURIComponent(name)}`,
  );
}

/**
 * 编辑模式手动重截插图：从某张源图按框裁一块存进文档 images/，返回引用路径。
 *
 * 返回的 ``asset_path`` 是 markdown 相对引用（``images/manual_N.jpg``）；
 * 调用方据此在编辑器里插入图片。
 */
export async function cropFigure(
  taskId: string,
  body: {
    source_filename: string;
    /** 矩形裁剪框；与 quad 二选一（quad 优先做透视校正）。 */
    box?: CropBox | undefined;
    /** 四角校正点；提供时后端透视矫正为正视图。 */
    quad?: CropQuad | undefined;
    doc_dir?: string | undefined;
  },
): Promise<CropFigureResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/crop-figure`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return handleResponse(response, CropFigureResponseSchema);
}

/** 检测 image_dir 下每张图的建议正文裁剪框（供"裁剪预览 + 拖拽微调"） */
export async function detectCropBoxes(
  imageDir: string,
): Promise<CropDetectResponse> {
  const response = await fetch(`${API_BASE}/crop/detect`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ image_dir: imageDir }),
  });
  return handleResponse(response, CropDetectResponseSchema);
}

/** 查询任务状态 */
export async function getTask(taskId: string): Promise<TaskResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, TaskResponseSchema);
}

/** 获取任务结果 */
export async function getTaskResult(
  taskId: string,
): Promise<TaskResultResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/result`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, TaskResultResponseSchema);
}

/** 查询任务列表 */
export async function listTasks(
  params: { status?: string | undefined; page?: number | undefined; page_size?: number | undefined } = {},
): Promise<TaskListResponse> {
  const query = new URLSearchParams();
  if (params.status !== undefined) query.set("status", params.status);
  if (params.page !== undefined) query.set("page", params.page.toString());
  if (params.page_size !== undefined)
    query.set("page_size", params.page_size.toString());
  const qs = query.toString();
  const url = qs ? `${API_BASE}/tasks?${qs}` : `${API_BASE}/tasks`;
  const response = await fetch(url, { headers: apiHeaders() });
  return handleResponse(response, TaskListResponseSchema);
}

/** 取消任务 */
export async function cancelTask(taskId: string): Promise<ActionResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/cancel`, {
    method: "POST",
    headers: apiHeaders(),
  });
  return handleResponse(response, ActionResponseSchema);
}

/** 删除任务 */
export async function deleteTask(taskId: string): Promise<ActionResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}`, {
    method: "DELETE",
    headers: apiHeaders(),
  });
  return handleResponse(response, ActionResponseSchema);
}

/**
 * 批量清理指定状态的任务（仅允许 completed / failed）。
 *
 * 返回 {deleted, failed, deleted_ids, errors}；调用方据此给用户反馈。
 */
export async function cleanupTasks(
  statuses: readonly ("completed" | "failed")[],
): Promise<TaskCleanupResponse> {
  const response = await fetch(`${API_BASE}/tasks/cleanup`, {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ statuses }),
  });
  return handleResponse(response, TaskCleanupResponseSchema);
}

/** 重试任务（从头跑） */
export async function retryTask(taskId: string): Promise<ActionResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/retry`, {
    method: "POST",
    headers: apiHeaders(),
  });
  return handleResponse(response, ActionResponseSchema);
}

/** 继续失败任务（复用 output_dir，OCR 跳过已完成图） */
export async function resumeTask(taskId: string): Promise<ActionResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/resume`, {
    method: "POST",
    headers: apiHeaders(),
  });
  return handleResponse(response, ActionResponseSchema);
}

/**
 * 下载结果 zip 的 URL（附加 token 供 <a href> 直接使用）。
 *
 * Epic D：``formats`` 非空时拼 ``?formats=docx,pdf``，后端把 document.md 按需
 * 导出成对应格式一并打进 zip。空 / 省略则纯 markdown zip（行为不变）。
 */
export function getDownloadUrl(
  taskId: string,
  formats?: readonly string[],
): string {
  const base = `${API_BASE}/tasks/${taskId}/download`;
  if (formats !== undefined && formats.length > 0) {
    const qs = new URLSearchParams({ formats: formats.join(",") }).toString();
    return appendTokenToUrl(`${base}?${qs}`);
  }
  return appendTokenToUrl(base);
}

/** 构建 assets URL（附加 token 供 <img src> 直接使用） */
export function getAssetUrl(taskId: string, assetPath: string): string {
  return appendTokenToUrl(`${API_BASE}/tasks/${taskId}/assets/${assetPath}`);
}

/** 获取全部文档结果（多文档） */
export async function getTaskResults(
  taskId: string,
): Promise<TaskResultsResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/results`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, TaskResultsResponseSchema);
}

/** 更新文档 Markdown 内容（人工精修） */
export async function updateResultMarkdown(
  taskId: string,
  resultIndex: number,
  markdown: string,
): Promise<ActionResponse> {
  const response = await fetch(
    `${API_BASE}/tasks/${taskId}/results/${resultIndex.toString()}`,
    {
      method: "PUT",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ markdown }),
    },
  );
  return handleResponse(response, ActionResponseSchema);
}

/** 获取代码模式 files-index.json；任务非代码模式 → 抛 HTTP 404 错误 */
export async function getFilesIndex(taskId: string): Promise<FilesIndex> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/files-index`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, FilesIndexSchema);
}

/** 获取代码模式单文件内容（text/plain） */
export async function getCodeFileContent(
  taskId: string,
  filePath: string,
): Promise<string> {
  const url = `${API_BASE}/tasks/${taskId}/files/${filePath
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/")}`;
  const response = await fetch(url, { headers: apiHeaders() });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status.toString()}: ${text}`);
  }
  return response.text();
}

/** 保存代码模式单文件内容 */
export async function updateCodeFileContent(
  taskId: string,
  filePath: string,
  content: string,
): Promise<ActionResponse> {
  const url = `${API_BASE}/tasks/${taskId}/files/${filePath
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/")}`;
  const response = await fetch(url, {
    method: "PUT",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ content }),
  });
  return handleResponse(response, ActionResponseSchema);
}

/** 对代码模式单文件草稿做实时诊断 */
export async function diagnoseCodeFileContent(
  taskId: string,
  filePath: string,
  content: string,
): Promise<DiagnoseCodeFileResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/code-diagnostics`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ file_path: filePath, content }),
  });
  return handleResponse(response, DiagnoseCodeFileResponseSchema);
}

/** 获取源图片列表 */
export async function listSourceImages(
  taskId: string,
): Promise<SourceImagesResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/source-images`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, SourceImagesResponseSchema);
}

/**
 * 获取任务版面高亮载荷（Epic E：编辑器光标↔原图 bbox 高亮）。
 *
 * 无 sidecar（非 VL 引擎 / 老任务 / 文档模式未产出版面）→ 后端 404 →
 * 返回 undefined（前端不高亮、不弹错误）；多文档可传 docDir 取对应子目录。
 */
export async function getTaskLayout(
  taskId: string,
  docDir?: string,
): Promise<LayoutPayload | undefined> {
  const query =
    docDir !== undefined && docDir !== ""
      ? `?doc_dir=${encodeURIComponent(docDir)}`
      : "";
  const response = await fetch(`${API_BASE}/tasks/${taskId}/layout${query}`, {
    headers: apiHeaders(),
  });
  if (response.status === 404) {
    return undefined;
  }
  return handleResponse(response, LayoutPayloadSchema);
}

/** 构建源图片 URL（附加 token 供 <img src> 直接使用） */
export function getSourceImageUrl(taskId: string, filename: string): string {
  return appendTokenToUrl(
    `${API_BASE}/tasks/${taskId}/source-images/${encodeURIComponent(filename)}`,
  );
}

/** 构建上传预览图 URL（附加 token 供 <img src> 在 token 模式下显示，#47） */
export function getUploadFileUrl(sessionId: string, fileId: string): string {
  return appendTokenToUrl(`${API_BASE}/uploads/${sessionId}/files/${fileId}`);
}

/** 构建 WS 进度推送 URL（附加 token 供 WebSocket 握手使用） */
export function getWsProgressUrl(taskId: string): string {
  const protocol = globalThis.location.protocol === "https:" ? "wss:" : "ws:";
  const base = `${protocol}//${globalThis.location.host}${API_BASE}/tasks/${taskId}/progress`;
  const token = loadApiToken();
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

/** 浏览服务器目录（includeFiles=true 时同时返回目录和图片文件） */
export async function browseDirs(
  path?: string,
  includeFiles = false,
): Promise<BrowseDirsResponse> {
  const query = new URLSearchParams();
  if (path !== undefined) query.set("path", path);
  if (includeFiles) query.set("include_files", "true");
  const qs = query.toString();
  const url = qs ? `${API_BASE}/filesystem/dirs?${qs}` : `${API_BASE}/filesystem/dirs`;
  const response = await fetch(url, { headers: apiHeaders() });
  return handleResponse(response, BrowseDirsResponseSchema);
}

/** 将服务器上已有文件 stage 为临时 image_dir */
export async function stageServerSources(
  paths: string[],
): Promise<StageServerSourceResponse> {
  const response = await fetch(`${API_BASE}/sources/server`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ paths }),
  });
  return handleResponse(response, StageServerSourceResponseSchema);
}

/** 创建上传会话 */
export async function createUploadSession(): Promise<UploadSessionResponse> {
  const response = await fetch(`${API_BASE}/uploads`, {
    method: "POST",
    headers: apiHeaders(),
  });
  return handleResponse(response, UploadSessionResponseSchema);
}

/** 上传文件到会话（可选保留目录结构，可通过 signal 取消） */
export async function uploadFiles(
  sessionId: string,
  files: File[],
  relativePaths?: readonly string[],
  signal?: AbortSignal,
): Promise<UploadFilesResponse> {
  const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
  const sizeMb = (totalBytes / 1024 / 1024).toFixed(1);
  const startedAt = Date.now();
  const filenamesPreview =
    files.slice(0, 3).map((f) => f.name).join(", ") +
    (files.length > 3 ? ` …(+${(files.length - 3).toString()})` : "");

  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  if (relativePaths !== undefined) {
    for (const p of relativePaths) {
      formData.append("paths", p);
    }
  }

  let response: Response;
  try {
    const init: RequestInit = {
      method: "POST",
      headers: apiHeaders(),
      body: formData,
    };
    if (signal !== undefined) init.signal = signal;
    response = await fetch(`${API_BASE}/uploads/${sessionId}/files`, init);
  } catch (error_: unknown) {
    /* AbortError 透传给 hook 层做"用户取消"分支 */
    if (error_ instanceof DOMException && error_.name === "AbortError") {
      throw error_;
    }
    const elapsedMs = Date.now() - startedAt;
    const detailMsg = error_ instanceof Error ? error_.message : String(error_);
    /* 写一条结构化 console.error，便于在 F12 直接查诊断细节 */
    console.error("[uploadFiles] 网络层失败 — 浏览器未拿到 HTTP 响应", {
      sessionId,
      fileCount: files.length,
      totalBytes,
      elapsedMs,
      filenames: files.map((f) => f.name),
      cause: error_,
    });
    throw new ApiError(
      `上传失败（${files.length.toString()} 张 / ${sizeMb} MB / ${elapsedMs.toString()}ms）：${detailMsg}`,
      {
        kind: "network",
        messageKey: "errors.client.uploadNetworkFailed",
        messageKeyParams: {
          count: files.length,
          sizeMb,
          elapsedMs,
          detail: detailMsg,
        },
        hintKey: "errors.client.uploadNetworkFailedHint",
        params: { filenames: filenamesPreview },
        cause: error_,
      },
    );
  }
  return handleResponse(response, UploadFilesResponseSchema);
}

/** 查询上传会话中的文件列表 */
export async function getUploadSessionFiles(
  sessionId: string,
): Promise<UploadSessionFilesResponse> {
  const response = await fetch(`${API_BASE}/uploads/${sessionId}/files`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, UploadSessionFilesResponseSchema);
}

/** 删除上传会话中的单个文件 */
export async function deleteUploadSessionFile(
  sessionId: string,
  fileId: string,
): Promise<UploadSessionFileDeleteResponse> {
  const response = await fetch(`${API_BASE}/uploads/${sessionId}/files/${fileId}`, {
    method: "DELETE",
    headers: apiHeaders(),
  });
  return handleResponse(response, UploadSessionFileDeleteResponseSchema);
}

/** 完成上传会话 */
export async function completeUpload(
  sessionId: string,
): Promise<UploadCompleteResponse> {
  const response = await fetch(`${API_BASE}/uploads/${sessionId}/complete`, {
    method: "POST",
    headers: apiHeaders(),
  });
  return handleResponse(response, UploadCompleteResponseSchema);
}

/** 查询 OCR 引擎状态 */
export async function getOcrStatus(): Promise<OcrStatusResponse> {
  const response = await fetch(`${API_BASE}/ocr/status`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, OcrStatusResponseSchema);
}

/** 预热 OCR 引擎；gpuId 为空字符串 → 后端 pick_best_gpu 自动选 */
export async function warmupOcrEngine(
  model: string,
  gpuId: string,
): Promise<OcrWarmupResponse> {
  const body: { model: string; gpu_id?: string } = { model };
  if (gpuId !== "") body.gpu_id = gpuId;
  const response = await fetch(`${API_BASE}/ocr/warmup`, {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return handleResponse(response, OcrWarmupResponseSchema);
}

/** 枚举系统可见的 GPU + 推荐索引 */
export async function listGpus(): Promise<GpuListResponse> {
  const response = await fetch(`${API_BASE}/gpus`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, GpuListResponseSchema);
}

/** 探测本地 NER 可用性（spaCy + 模型是否就绪）；不加载模型，廉价。 */
export async function getNerStatus(): Promise<NerStatusResponse> {
  const response = await fetch(`${API_BASE}/ner/status`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, NerStatusResponseSchema);
}

/**
 * 一键安装本地 NER 环境（spaCy + 缺失模型，装进后端当前 venv）。
 *
 * 后端单任务串行：已有安装在跑会抛 ``ApiError``（code=NER_SETUP_IN_PROGRESS，
 * HTTP 409）；调用方据此直接转入轮询即可。返回受理时的安装状态。
 */
export async function startNerSetup(): Promise<NerSetupStatusResponse> {
  const response = await fetch(`${API_BASE}/ner/setup`, {
    method: "POST",
    headers: apiHeaders(),
  });
  return handleResponse(response, NerSetupStatusResponseSchema);
}

/** 轮询本地 NER 环境安装进度（state / log / error）。 */
export async function getNerSetupStatus(): Promise<NerSetupStatusResponse> {
  const response = await fetch(`${API_BASE}/ner/setup/status`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, NerSetupStatusResponseSchema);
}
