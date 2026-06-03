## Why

PPT 屏摄照片（会议幻灯片投影在 LED 屏上的强透视实拍）目前无法还原：文档模式的跨页去重是为长文档连续内容设计的，套用到"每页独立幻灯片"会误删重复版式；代码模式走行级 OCR，没有版面分析与图形裁图能力。S0 选型（AGE-84）已确认 PaddleOCR-VL-1.6 + 透视矫正可达 ~95% 命中，需据此新增 PPT 还原模式。

## What Changes

- 新增流式 Pipeline **第三消费者分支 `_ppt_pipeline`**，与文档模式 `_stream_process`、代码模式 `_code_pipeline` **互斥三选一**，共享 `_ocr_producer` + `page_queue`。
- 新增 **S2 透视矫正前处理**（OpenCV：Otsu 亮区 → 最大轮廓 → 4 角 → warpPerspective，顶边上抬补暗标题栏），落盘 before/after 对照证据，检测失败回退原图不中断。
- 复用 **VL-1.6 `doc_parser`**（`vl` pipeline）做单页版面识别、公式 LaTeX、图形区域（化学结构/分子模型/图表）自动裁成 `images/`。
- 新增 **S4 逐页保序组装**：单页区域按阅读顺序 → 多页按原文件序合并为单 `document.md`（**不跨页去重**），复用文档模式两阶段图片引用。可选 LLM 轻润色（默认关，前端可开）。
- 新增 `PowerPointRestoreConfig`（后端）+ `PowerPointRestoreConfigRequest`（请求级覆盖）+ 路由合成 + **模式互斥校验** + 任务快照/hydrate + DB `ppt` 列（同 `code` 列机制）。
- 前端模式选择改为 **radio 三选一**（文档/代码/PPT），PPT 模式透出 LLM 润色开关 + i18n。
- **非破坏**：文档模式、代码模式既有行为完全不变；PPT 为纯新增分支。

## Capabilities

### New Capabilities

- `ppt-perspective-rectify`: 屏摄幻灯片四边形检测 + 透视矫正为正视图，落盘对照证据，失败回退原图（对应 S2 / AGE-86）。
- `ppt-page-recognition`: VL-1.6 `doc_parser` 单页版面识别、文字与公式转 markdown/LaTeX、图形区域裁成图片（对应 S3 / AGE-87）。
- `ppt-document-assembly`: 单页保序组装 + 多页按原序合并 `document.md` + 两阶段图片引用 + 可选轻润色（对应 S4 / AGE-88）。
- `ppt-mode-integration`: config / 请求 schema / 路由合成 / 任务持久化 / pipeline 分支 / 前端三选一互斥 / DB migration（对应 S5 / AGE-89）。

### Modified Capabilities

（无。`openspec/specs/` 为空——本变更是项目首次引入 OpenSpec；文档/代码模式无既存 spec，PPT 模式为纯新增能力，不修改任何现有 requirement。）

## Impact

- **后端**：`pipeline/config.py`（新增 config + `PipelineConfig.ppt`）、`api/schemas.py`、`api/routes.py`（合成 + 互斥校验）、`pipeline/task_manager.py`（快照/hydrate + DB 列）、`pipeline/pipeline.py`（`process_tree/process_many/_stream_pipeline` 签名 + 分支 + producer 矫正 hook）。
- **新增文件**：`processing/slide_rectify.py`（S2）、`output/ppt_renderer.py`（S4）。
- **前端**：`components/TaskForm.tsx`（radio 三选一 + 润色开关）、`useTaskRunner`、`i18n/{en,zh-CN,zh-TW}.ts`。
- **数据库**：新增 `ppt` JSON 列 + migration（与现有 `code` 列同套机制，老任务无需手动迁移）。
- **依赖**：透视矫正需要 OpenCV（`cv2`）——接入前确认是否已在 `pyproject.toml`，缺则补。
- **引擎**：复用已集成的 PaddleOCR-VL-1.6（`vl` pipeline），无新引擎、无新模型权重。
- **设计真相源**：`docs/zh/ppt-mode.md`（S1 设计文档，已含 17 处接入点 文件:行 + 函数签名草案 + 两张架构/流水线图）。
