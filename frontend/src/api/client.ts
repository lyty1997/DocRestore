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
  UploadCompleteResponseSchema,
  UploadFilesResponseSchema,
  UploadSessionFileDeleteResponseSchema,
  UploadSessionFilesResponseSchema,
  UploadSessionResponseSchema,
  OcrStatusResponseSchema,
  OcrWarmupResponseSchema,
  GpuListResponseSchema,
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
  type UploadCompleteResponse,
  type UploadFilesResponse,
  type UploadSessionFileDeleteResponse,
  type UploadSessionFilesResponse,
  type UploadSessionResponse,
  type OcrStatusResponse,
  type OcrWarmupResponse,
  type GpuListResponse,
} from "./schemas";
import { appendTokenToUrl, getAuthHeaders, loadApiToken } from "./auth";

/** API 基础路径（开发环境通过 Vite proxy 转发） */
const API_BASE = "/api/v1";

/** 创建任务请求体 */
interface CreateTaskBody {
  image_dir: string;
  output_dir?: string | undefined;
  llm?: {
    model?: string | undefined;
    api_base?: string | undefined;
    api_key?: string | undefined;
    max_chars_per_segment?: number | undefined;
  } | undefined;
  pii?: {
    enable?: boolean | undefined;
    custom_sensitive_words?:
      | readonly { word: string; code?: string | undefined }[]
      | undefined;
  } | undefined;
  ocr?: {
    model?: string | undefined;
    gpu_id?: string | undefined;
  } | undefined;
  code?: {
    enable: boolean;
    output_files_dir?: string | undefined;
  } | undefined;
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
    throw new ApiError(
      `HTTP ${response.status.toString()}: ${fallback}`,
      {
        kind: "http",
        httpStatus: response.status,
        ...(parsed.code === undefined ? {} : { code: parsed.code }),
        params: parsed.params,
        ...(hintKeyForStatus(response.status) === undefined
          ? {}
          : { hintKey: hintKeyForStatus(response.status) }),
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

/** 下载结果 zip 的 URL（附加 token 供 <a href> 直接使用） */
export function getDownloadUrl(taskId: string): string {
  return appendTokenToUrl(`${API_BASE}/tasks/${taskId}/download`);
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

/** 获取源图片列表 */
export async function listSourceImages(
  taskId: string,
): Promise<SourceImagesResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/source-images`, {
    headers: apiHeaders(),
  });
  return handleResponse(response, SourceImagesResponseSchema);
}

/** 构建源图片 URL（附加 token 供 <img src> 直接使用） */
export function getSourceImageUrl(taskId: string, filename: string): string {
  return appendTokenToUrl(
    `${API_BASE}/tasks/${taskId}/source-images/${encodeURIComponent(filename)}`,
  );
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
    response = await fetch(`${API_BASE}/uploads/${sessionId}/files`, {
      method: "POST",
      headers: apiHeaders(),
      body: formData,
      signal,
    });
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
