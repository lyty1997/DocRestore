/**
 * API 响应 zod schema 定义
 *
 * 与后端 schemas.py 保持一致，所有外部输入必须经过运行时校验。
 */

import { z } from "zod/v4";

/** 进度信息 */
export const ProgressResponseSchema = z.object({
  stage: z.string(),
  current: z.number(),
  total: z.number(),
  percent: z.number(),
  message: z.string(),
});
export type ProgressResponse = z.infer<typeof ProgressResponseSchema>;

/** 创建任务响应 */
export const CreateTaskResponseSchema = z.object({
  task_id: z.string(),
  status: z.string(),
});
export type CreateTaskResponse = z.infer<typeof CreateTaskResponseSchema>;

/** 任务状态响应 */
export const TaskResponseSchema = z.object({
  task_id: z.string(),
  status: z.string(),
  progress: ProgressResponseSchema.nullable().optional(),
  error: z.string().nullable().optional(),
});
export type TaskResponse = z.infer<typeof TaskResponseSchema>;

/** 任务结果响应 */
export const TaskResultResponseSchema = z.object({
  task_id: z.string(),
  output_path: z.string(),
  markdown: z.string(),
});
export type TaskResultResponse = z.infer<typeof TaskResultResponseSchema>;

/** WS 推送的进度消息（与 TaskProgress 一致） */
export const TaskProgressSchema = ProgressResponseSchema;
export type TaskProgress = ProgressResponse;
