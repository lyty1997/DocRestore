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

/** 统一错误处理 */
async function handleResponse<T>(
  response: Response,
  schema: { parse: (data: unknown) => T },
): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status.toString()}: ${text}`);
  }
  const json: unknown = await response.json();
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
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  if (relativePaths !== undefined) {
    for (const p of relativePaths) {
      formData.append("paths", p);
    }
  }
  const response = await fetch(`${API_BASE}/uploads/${sessionId}/files`, {
    method: "POST",
    headers: apiHeaders(),
    body: formData,
    signal,
  });
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
