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
  /** 服务端拼好的中文消息，i18n 无 key 时 fallback 展示 */
  message: z.string(),
  /** 子目录标识（process_tree 并行时每一路的 rel path，空=主进度） */
  subtask: z.string().default(""),
  /** i18n 入口 key，如 "progress.refineStream"；空=无结构化文案 */
  message_key: z.string().default(""),
  /** i18n 插值参数（值统一 str） */
  message_params: z.record(z.string(), z.string()).default({}),
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
  doc_title: z.string().optional(),
  doc_dir: z.string().optional(),
  /**
   * 子文档级错误（2026-04-21 引入）。非空表示此子文档处理失败，
   * markdown 可能为空，前端应显示 error 而非渲染 markdown。
   * 后端老版本无此字段时默认为空字符串。
   */
  error: z.string().default(""),
});
export type TaskResultResponse = z.infer<typeof TaskResultResponseSchema>;

/** 多文档结果响应 */
export const TaskResultsResponseSchema = z.object({
  task_id: z.string(),
  results: z.array(TaskResultResponseSchema),
});
export type TaskResultsResponse = z.infer<typeof TaskResultsResponseSchema>;

/** 任务列表项 */
export const TaskListItemSchema = z.object({
  task_id: z.string(),
  status: z.string(),
  image_dir: z.string(),
  output_dir: z.string(),
  error: z.string().nullable().optional(),
  created_at: z.string(),
  result_count: z.number(),
});
export type TaskListItem = z.infer<typeof TaskListItemSchema>;

/** 任务列表响应（分页） */
export const TaskListResponseSchema = z.object({
  tasks: z.array(TaskListItemSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
});
export type TaskListResponse = z.infer<typeof TaskListResponseSchema>;

/** 源图片列表响应 */
export const SourceImagesResponseSchema = z.object({
  task_id: z.string(),
  images: z.array(z.string()),
});
export type SourceImagesResponse = z.infer<typeof SourceImagesResponseSchema>;

/** 操作响应（取消/删除/重试） */
export const ActionResponseSchema = z.object({
  task_id: z.string(),
  message: z.string().optional(),
});
export type ActionResponse = z.infer<typeof ActionResponseSchema>;

/** 批量清理任务响应 */
export const TaskCleanupResponseSchema = z.object({
  deleted: z.number().default(0),
  failed: z.number().default(0),
  deleted_ids: z.array(z.string()).default([]),
  errors: z.array(z.string()).default([]),
});
export type TaskCleanupResponse = z.infer<typeof TaskCleanupResponseSchema>;

/** WS 推送的进度消息（与 TaskProgress 一致） */
export const TaskProgressSchema = ProgressResponseSchema;
export type TaskProgress = ProgressResponse;

/** 目录/文件条目 */
export const DirEntrySchema = z.object({
  name: z.string(),
  is_dir: z.boolean(),
  size_bytes: z.number().nullable().optional(),
  image_count: z.number().nullable().optional(),
});
export type DirEntry = z.infer<typeof DirEntrySchema>;

/** 目录浏览响应（entries 可同时包含目录和文件） */
export const BrowseDirsResponseSchema = z.object({
  path: z.string(),
  parent: z.string().nullable().optional(),
  entries: z.array(DirEntrySchema),
});
export type BrowseDirsResponse = z.infer<typeof BrowseDirsResponseSchema>;

/** 服务器源 stage 响应 */
export const StageServerSourceResponseSchema = z.object({
  image_dir: z.string(),
  file_count: z.number(),
});
export type StageServerSourceResponse = z.infer<
  typeof StageServerSourceResponseSchema
>;

/** 上传会话响应 */
export const UploadSessionResponseSchema = z.object({
  session_id: z.string(),
  max_file_size_mb: z.number(),
  allowed_extensions: z.array(z.string()),
});
export type UploadSessionResponse = z.infer<typeof UploadSessionResponseSchema>;

/** 上传文件响应 */
export const UploadFilesResponseSchema = z.object({
  session_id: z.string(),
  uploaded: z.array(z.string()),
  total_uploaded: z.number(),
  failed: z.array(z.string()),
});
export type UploadFilesResponse = z.infer<typeof UploadFilesResponseSchema>;

/** 上传会话文件条目 */
export const UploadFileItemSchema = z.object({
  session_id: z.string(),
  file_id: z.string(),
  filename: z.string(),
  relative_path: z.string(),
  size_bytes: z.number(),
  created_at: z.string(),
});
export type UploadFileItem = z.infer<typeof UploadFileItemSchema>;

/** 上传会话文件列表响应 */
export const UploadSessionFilesResponseSchema = z.object({
  session_id: z.string(),
  files: z.array(UploadFileItemSchema),
});
export type UploadSessionFilesResponse = z.infer<typeof UploadSessionFilesResponseSchema>;

/** 上传会话单文件删除响应 */
export const UploadSessionFileDeleteResponseSchema = z.object({
  session_id: z.string(),
  file_id: z.string(),
  remaining_count: z.number(),
});
export type UploadSessionFileDeleteResponse = z.infer<typeof UploadSessionFileDeleteResponseSchema>;

/** 完成上传响应 */
export const UploadCompleteResponseSchema = z.object({
  session_id: z.string(),
  image_dir: z.string(),
  file_count: z.number(),
  total_size_bytes: z.number(),
});
export type UploadCompleteResponse = z.infer<typeof UploadCompleteResponseSchema>;

/** OCR 引擎状态响应 */
export const OcrStatusResponseSchema = z.object({
  current_model: z.string(),
  current_gpu: z.string(),
  /** 后端 2026-04-21 起新增字段；旧后端缺字段时 z.string().default 回退为 "" */
  current_gpu_name: z.string().default(""),
  is_ready: z.boolean(),
  is_switching: z.boolean(),
});
export type OcrStatusResponse = z.infer<typeof OcrStatusResponseSchema>;

/** 单张 GPU 的元信息（GET /gpus 响应元素） */
export const GpuInfoSchema = z.object({
  index: z.string(),
  name: z.string(),
  memory_total_mb: z.number(),
  memory_free_mb: z.number().nullable().optional(),
  compute_capability: z.string().nullable().optional(),
});
export type GpuInfo = z.infer<typeof GpuInfoSchema>;

/** GET /gpus 响应：GPU 列表 + 推荐索引 */
export const GpuListResponseSchema = z.object({
  gpus: z.array(GpuInfoSchema),
  recommended: z.string().nullable().optional(),
});
export type GpuListResponse = z.infer<typeof GpuListResponseSchema>;

/** OCR 引擎预热响应 */
export const OcrWarmupResponseSchema = z.object({
  status: z.string(),
  message: z.string(),
});
export type OcrWarmupResponse = z.infer<typeof OcrWarmupResponseSchema>;

/** 代码模式 files-index 单条记录 */
export const FilesIndexEntrySchema = z.object({
  path: z.string(),
  filename: z.string(),
  language: z.string().nullable().optional(),
  source_pages: z.array(z.string()).default([]),
  line_count: z.number().default(0),
  line_no_range: z.array(z.number()).default([]),
  flags: z.array(z.string()).default([]),
  /** 由 scripts/age8_compile_check.py 写入；可缺省 */
  compile_status: z
    .enum(["passed", "failed", "skipped"])
    .nullable()
    .optional(),
  compile_error: z.string().nullable().optional(),
  compile_skip_reason: z.string().nullable().optional(),
  compile_failing_lines: z.array(z.number()).nullable().optional(),
});
export type FilesIndexEntry = z.infer<typeof FilesIndexEntrySchema>;

/** files-index.json 整个数组 */
export const FilesIndexSchema = z.array(FilesIndexEntrySchema);
export type FilesIndex = z.infer<typeof FilesIndexSchema>;
