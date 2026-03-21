/**
 * API 客户端：封装 fetch 调用 + zod 校验
 */

import {
  CreateTaskResponseSchema,
  TaskResponseSchema,
  TaskResultResponseSchema,
  type CreateTaskResponse,
  type TaskResponse,
  type TaskResultResponse,
} from "./schemas";

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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse(response, CreateTaskResponseSchema);
}

/** 查询任务状态 */
export async function getTask(taskId: string): Promise<TaskResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}`);
  return handleResponse(response, TaskResponseSchema);
}

/** 获取任务结果 */
export async function getTaskResult(
  taskId: string,
): Promise<TaskResultResponse> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/result`);
  return handleResponse(response, TaskResultResponseSchema);
}

/** 下载结果 zip 的 URL */
export function getDownloadUrl(taskId: string): string {
  return `${API_BASE}/tasks/${taskId}/download`;
}

/** 构建 assets URL（用于图片引用重写） */
export function getAssetUrl(taskId: string, assetPath: string): string {
  return `${API_BASE}/tasks/${taskId}/assets/${assetPath}`;
}

/** 构建 WS 进度推送 URL */
export function getWsProgressUrl(taskId: string): string {
  const protocol = globalThis.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${globalThis.location.host}${API_BASE}/tasks/${taskId}/progress`;
}
