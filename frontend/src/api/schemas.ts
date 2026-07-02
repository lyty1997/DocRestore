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
  /** 该任务是否启用 LLM 精修；缺失（旧后端）按 true 兼容 */
  enable_refine: z.boolean().default(true),
  /** 处理模式：关精修时文档模式隐藏「LLM 精修」轨、PPT/代码改名「后处理」 */
  mode: z.enum(["doc", "code", "ppt"]).default("doc"),
});
export type TaskResponse = z.infer<typeof TaskResponseSchema>;
/** 处理模式（与后端 TaskResponse.mode / 表单三选一对齐） */
export type ProcessingMode = TaskResponse["mode"];

/** 软降级警告载荷（#96）：i18n code + 参数，前端按 code 本地化渲染。
 *  与后端 WarningPayload 同构；code="legacy" 时 params.text 为旧任务原中文串（直接显示）。 */
export const WarningPayloadSchema = z.object({
  code: z.string(),
  params: z
    .record(z.string(), z.union([z.string(), z.number()]))
    .default({}),
});
export type WarningPayload = z.infer<typeof WarningPayloadSchema>;

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
  /**
   * 软降级警告（#96，非致命，结构化 code+params）：VL 退本地推理 / PDF 缺页 /
   * 段截断等。error 为空但 warnings 非空 = 文档可用但有降级，前端显示软提示而非
   * 失败态。旧后端缺字段时 default 回退为空数组。
   */
  warnings: z.array(WarningPayloadSchema).default([]),
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
  /**
   * 降级原因码（#96，空=未降级）：请求 VL 但退回本地推理时为
   * vl_no_server_python / vl_server_python_missing。旧后端缺字段时回退为 ""。
   */
  degraded_reason: z.string().default(""),
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

/** 本地 NER 可用性探测响应（GET /ner/status，不加载模型）。
 *
 * 前端在开启 PII（人名/机构名脱敏默认随之开启）时拉取；``available=false``
 * 时弹「一键配置本地 NER 环境」入口并在配好前禁止提交。 */
export const NerStatusResponseSchema = z.object({
  available: z.boolean(),
  spacy_installed: z.boolean(),
  configured_models: z.array(z.string()),
  installed_models: z.array(z.string()),
  missing_models: z.array(z.string()),
});
export type NerStatusResponse = z.infer<typeof NerStatusResponseSchema>;

/** 本地 NER 环境安装状态（POST /ner/setup 受理 + GET /ner/setup/status 轮询）。 */
export const NerSetupStatusResponseSchema = z.object({
  state: z.enum(["idle", "running", "done", "failed"]),
  log: z.array(z.string()),
  error: z.string(),
});
export type NerSetupStatusResponse = z.infer<
  typeof NerSetupStatusResponseSchema
>;

/** 代码模式 files-index 单条记录 */
export const SourcePageRangeSchema = z.object({
  page: z.string(),
  start_line: z.number(),
  end_line: z.number(),
});
export type SourcePageRange = z.infer<typeof SourcePageRangeSchema>;

export const CodeDiagnosticItemSchema = z.object({
  line: z.number(),
  column: z.number().default(0),
  severity: z.string().default("error"),
  category: z.string().default("syntax"),
  code: z.string().default(""),
  message: z.string().default(""),
  source: z.string().default(""),
});
export type CodeDiagnosticItem = z.infer<typeof CodeDiagnosticItemSchema>;

export const CodeDiagnosticSchema = z.object({
  path: z.string().optional(),
  language: z.string().optional(),
  status: z.string(),
  category: z.string(),
  summary: z.string().default(""),
  failing_lines: z.array(z.number()).default([]),
  syntax_errors: z.number().default(0),
  semantic_errors: z.number().default(0),
  dependency_errors: z.number().default(0),
  items: z.array(CodeDiagnosticItemSchema).default([]),
  tool: z.string().default(""),
  duration_ms: z.number().default(0),
});
export type CodeDiagnostic = z.infer<typeof CodeDiagnosticSchema>;

export const DiagnoseCodeFileResponseSchema = CodeDiagnosticSchema;
export type DiagnoseCodeFileResponse = z.infer<
  typeof DiagnoseCodeFileResponseSchema
>;

export const FilesIndexEntrySchema = z.object({
  path: z.string(),
  filename: z.string(),
  language: z.string().nullable().optional(),
  source_pages: z.array(z.string()).default([]),
  source_page_ranges: z.array(SourcePageRangeSchema).default([]),
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
  diagnostic: CodeDiagnosticSchema.optional(),
});
export type FilesIndexEntry = z.infer<typeof FilesIndexEntrySchema>;

/** files-index.json 整个数组 */
export const FilesIndexSchema = z.array(FilesIndexEntrySchema);
export type FilesIndex = z.infer<typeof FilesIndexSchema>;

/** 正文裁剪框（原图像素坐标，左上 x0,y0 右下 x1,y1） */
export const CropBoxSchema = z.object({
  x0: z.number(),
  y0: z.number(),
  x1: z.number(),
  y1: z.number(),
});
export type CropBox = z.infer<typeof CropBoxSchema>;

/** 单个角点（原图像素坐标） */
export const CropPointSchema = z.object({
  x: z.number(),
  y: z.number(),
});
export type CropPoint = z.infer<typeof CropPointSchema>;

/** 四角点（原图像素坐标），顺序即角色：左上/右上/右下/左下（四角透视校正用） */
export const CropQuadSchema = z.object({
  tl: CropPointSchema,
  tr: CropPointSchema,
  br: CropPointSchema,
  bl: CropPointSchema,
});
export type CropQuad = z.infer<typeof CropQuadSchema>;

/** 单张图裁剪框检测结果；box=null 表示无需裁剪（已裁剪 / 无侧栏 / 检测失败） */
export const CropDetectItemSchema = z.object({
  name: z.string(),
  width: z.number(),
  height: z.number(),
  box: CropBoxSchema.nullable(),
});
export type CropDetectItem = z.infer<typeof CropDetectItemSchema>;

/** POST /crop/detect 响应 */
export const CropDetectResponseSchema = z.object({
  images: z.array(CropDetectItemSchema),
});
export type CropDetectResponse = z.infer<typeof CropDetectResponseSchema>;

/** POST /tasks/{id}/crop-figure 响应：asset_path 为 markdown 相对引用 images/xxx.jpg */
export const CropFigureResponseSchema = z.object({
  asset_path: z.string(),
});
export type CropFigureResponse = z.infer<typeof CropFigureResponseSchema>;

/** 版面块：原图像素 bbox (x0,y0,x1,y1) + 类型 + raw OCR 文字（光标模糊匹配，Epic E） */
export const LayoutBlockPayloadSchema = z.object({
  bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]),
  label: z.string(),
  /** 阅读序（0-based）：后端按 blocks 列表位置 enumerate 派生；版面全览画 index+1 角标（E8/#92）。 */
  index: z.number(),
  text: z.string(),
  /** 图片/图表块输出引用 `images/{stem}_N.ext`（对齐 markdown <img src>），
   *  供前端按引用匹配光标所在图片块；文字块为空。旧 sidecar 无此字段 → 默认空。 */
  image_ref: z.string().default(""),
});
export type LayoutBlockPayload = z.infer<typeof LayoutBlockPayloadSchema>;

/** 单页版面：原图文件名 + 像素尺寸 (w,h)（% 换算分母）+ 块列表 */
export const LayoutPagePayloadSchema = z.object({
  filename: z.string(),
  image_size: z.tuple([z.number(), z.number()]),
  blocks: z.array(LayoutBlockPayloadSchema),
});
export type LayoutPagePayload = z.infer<typeof LayoutPagePayloadSchema>;

/** GET /tasks/{id}/layout 响应：各页块 bbox，供编辑器光标↔原图高亮（Epic E）。
 *  processed=true：bbox/image_size 在**处理图**坐标系（OCR 前做过 PPT 矫正 `_after` /
 *  content_crop 裁剪 `_crop`，§13/§15），前端须改显对应处理图才对齐（逐页尝试，无处理图
 *  的页 onError 回退原图）；缺省 false（无预处理，bbox=原图坐标，显原图）。 */
export const LayoutPayloadSchema = z.object({
  pages: z.array(LayoutPagePayloadSchema),
  processed: z.boolean().default(false),
});
export type LayoutPayload = z.infer<typeof LayoutPayloadSchema>;

/** 代码单行在原图的像素框 (x0,y0,x1,y1) + 来源页标识 + 行号（#93 悬停放大）。
 *  page = `{stem}.col{idx}`，对齐 files-index 的 source_page_ranges。 */
export const CodeLineBoxPayloadSchema = z.object({
  line_no: z.number(),
  page: z.string(),
  bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]),
});
export type CodeLineBoxPayload = z.infer<typeof CodeLineBoxPayloadSchema>;

/** 单源文件行级版面：path 对齐 files-index entry.path。
 *  line_map（#5）：按精修后正文行序（0-based）索引、值为该行对应的原 OCR line_no
 *  （= CodeLineBox.line_no 键），null = rewrite/repair 新增行→不放大；
 *  空数组 = 精修守恒（identity，前端按 displayLineNumber 直接查表，零回归）。 */
export const CodeFileLayoutPayloadSchema = z.object({
  path: z.string(),
  lines: z.array(CodeLineBoxPayloadSchema).default([]),
  line_map: z.array(z.number().nullable()).default([]),
});
export type CodeFileLayoutPayload = z.infer<typeof CodeFileLayoutPayloadSchema>;

/** GET /tasks/{id}/code-layout 响应：各源文件逐行 bbox，供悬停行↔原图局部放大（#93）。
 *  processed：代码模式手动裁剪（§14.1）后行 bbox 在裁剪图坐标系，放大镜须改显处理图对齐
 *  （与 LayoutPayload.processed 同义，逐页 onError 回退原图）。 */
export const CodeLayoutPayloadSchema = z.object({
  files: z.array(CodeFileLayoutPayloadSchema).default([]),
  processed: z.boolean().default(false),
});
export type CodeLayoutPayload = z.infer<typeof CodeLayoutPayloadSchema>;

/** GET /auth/info 响应：是否需要 token + token 来源（**绝不含 token 值**）。
 *  前端据此判断是否提示用户设置 token，并按 token_source 给出获取指引。
 *  token_source 与后端 auth.TokenSource 一一对应。 */
export const AuthInfoResponseSchema = z.object({
  auth_required: z.boolean(),
  token_source: z.enum(["env", "device_file", "insecure", "unknown"]),
  /** device token 文件真实路径（仅 device_file / unknown 来源提供，遵循 XDG /
   *  平台约定）；前端据此显示精确 cat 命令。缺失/env/insecure → null/undefined。 */
  token_file: z.string().nullish(),
});
export type AuthInfoResponse = z.infer<typeof AuthInfoResponseSchema>;
export type TokenSource = AuthInfoResponse["token_source"];
