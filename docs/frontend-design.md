<!--
Copyright 2026 @lyty1997

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# DocRestore 前端技术规格（AGE-12 / AGE-13）

状态：Draft

## 0. 需求追踪（Linear）

- AGE-12：WebSocket 实时进度推送（Backlog / Medium）
  - https://linear.app/agentic-graph-rag/issue/AGE-12/websocket-实时进度推送
- AGE-13：Web 前端界面（Backlog / Low）
  - https://linear.app/agentic-graph-rag/issue/AGE-13/web-前端界面

## 1. 背景与目标

DocRestore 当前以 REST API 提供“照片目录 → Markdown（含插图）”能力。AGE-12/AGE-13 的目标是在本地部署场景下，提供一个独立 Web 前端工程，实现：

- 创建任务（输入照片目录 `image_dir`）
- 实时进度展示（WebSocket 优先，失败自动降级轮询）
- 结果预览（Markdown 渲染 + 插图可见）
- 结果下载（zip：`document.md` + `images/`）

## 2. 非目标（MVP 不做）

- 远程部署/公网访问、用户体系、认证鉴权
- 浏览器端目录选择并上传图片（后续可做 multipart 上传方案）
- 多任务并发与任务历史持久化（刷新即丢失）
- 取消任务 / 删除任务 / 重试（后续迭代）
- SSE（仅 WS + 轮询）

## 3. 部署与信任边界（纯本地使用）

### 3.1 假设

- 前端与后端运行在同一台机器（或同一内网环境），用户对后端机器的文件系统路径具备“可信输入”。
- MVP 不做鉴权，因此**后端必须在文件访问接口中做严格路径约束**，避免被前端任意读文件。

### 3.2 风险控制（后端约束）

- `POST /tasks` 的 `image_dir`：必须校验“存在/是目录/只包含受支持后缀（jpg/jpeg/png，大小写敏感兼容）”。
- `assets` 接口：必须防 `..` / 软链接穿越；推荐只允许访问该 `task_id` 的 `output_dir` 子树（或进一步仅允许 `images/` 和 `document.md`）。

## 4. 核心用户路径（单页闭环）

1) 用户输入（或粘贴）照片目录路径 `image_dir`
2) 点击“开始处理” → 创建任务
3) 进入“处理中”状态：
   - 能看到阶段（ocr/clean/merge/refine/render）与百分比
   - WebSocket 失败时自动切到轮询
4) 任务完成：展示 Markdown 预览（含图）
5) 点击“下载结果（zip）”
6) 任务失败：展示错误摘要（可折叠显示 traceback，MVP 允许）

## 5. 信息架构（IA）与页面清单

MVP：单页（不做路由也可）。建议区域划分：

- 顶部：服务连接状态（后端 base URL、WS 状态、轮询状态）
- 输入区：`image_dir` 输入框 +（可选）`output_dir` 输入框 +（可选）LLM 覆盖字段
- 任务区：task_id、状态、进度条、阶段文本
- 结果区：Markdown 渲染 + 下载按钮
- 错误区：错误提示 +（可选）展开详情

## 6. 前端状态与数据流

### 6.1 状态模型（最小集）

- `taskId: string | null`
- `status: "idle" | "pending" | "processing" | "completed" | "failed"`
- `progress?: { stage; current; total; percent; message }`
- `resultMarkdown?: string`
- `wsState: "connecting" | "open" | "closed" | "error"`
- `pollingEnabled: boolean`

### 6.2 进度获取策略（WS 优先 + 降级轮询）

- 创建任务后：优先建立 `WS /api/v1/tasks/{task_id}/progress`
- 若 WS 建连失败（握手异常/超时/立即断开）：启动轮询 `GET /api/v1/tasks/{task_id}`
- 若 WS 已连接但后续断开：立即切回轮询，直到任务终态
- 到达终态（completed/failed）后：停止 WS 与轮询；如为 completed，再调用一次 `GET /result` 拉取结果

建议轮询间隔（MVP）：1s（任务结束后停止）。

## 7. 后端接口契约（REST，已存在）

> 以当前实现为准：`src/docrestore/api/routes.py` + `src/docrestore/api/schemas.py`

### 7.1 创建任务

- `POST /api/v1/tasks`
- Body：
  - `image_dir: string`（必填）
  - `output_dir?: string`（可选）
  - `llm?: { model?: string; api_base?: string; api_key?: string; max_chars_per_segment?: number }`
- Response：`{ task_id: string, status: string }`

### 7.2 查询任务

- `GET /api/v1/tasks/{task_id}`
- Response：`{ task_id, status, progress?, error? }`

### 7.3 获取结果

- `GET /api/v1/tasks/{task_id}/result`
- Response：`{ task_id, output_path, markdown }`

说明：`markdown` 中图片引用为 `images/{stem}_{idx}.jpg`（由 Renderer 输出阶段生成）。

## 8. 实时进度接口契约（WebSocket，AGE-12）

- Endpoint：`WS /api/v1/tasks/{task_id}/progress`
- 消息格式（MVP）：**裸 `TaskProgress` JSON**（与 `docs/design.md §5.2` 一致）

```json
{
  "stage": "ocr",
  "current": 5,
  "total": 26,
  "percent": 19.2,
  "message": "正在 OCR 第 5 张照片..."
}
```

语义约定（MVP）：
- 只承诺推送“最新进度快照”，不保证每一步都到达（允许合并/丢弃中间值）
- 终态（completed/failed）仍以 `GET /tasks/{task_id}` 为准（WS 可断线、也可能不发送终态事件）

## 9. 结果预览与下载（AGE-13 关键缺口）

### 9.1 assets：插图资源受限访问接口（需新增后端能力）

为了让浏览器加载 `images/*`，需要后端提供受限的静态资源访问。

- `GET /api/v1/tasks/{task_id}/assets/{asset_path:path}`
- 行为：从该任务的 `output_dir` 下读取文件并返回（`FileResponse`）
- 约束（必须）：
  - `asset_path` 必须是相对路径，不允许 `..`
  - 解析后的真实路径必须位于 `output_dir` 内
  - 推荐只允许：`document.md` 与 `images/**`（MVP 足够）

前端渲染 markdown 时，需要将 `images/xxx.jpg` 重写为：

- `/api/v1/tasks/{task_id}/assets/images/xxx.jpg`

### 9.2 download：下载 zip（MVP 必须）

- `GET /api/v1/tasks/{task_id}/download`
- Response：`application/zip`
- zip 内容结构：

```
document.md
images/
  <stem>_<idx>.jpg
```

约束（必须）：
- zip 中的 `document.md` 内图片引用必须是相对路径 `images/...`（当前 Renderer 已满足）

## 10. 技术选型与工程结构（前端独立项目）

本节不强制框架，但强制质量约束：

- TypeScript 必须 strict（并开启 `noUncheckedIndexedAccess` 等严格选项）
- 外部输入（API 响应）必须做运行时校验（推荐 zod），并从 schema 推导类型
- 进度流（WS/轮询）必须可取消，组件卸载时清理连接与定时器

推荐（便于落地、不过度工程）：
- Vite + React + TypeScript
- Markdown 渲染：`react-markdown`（或任意等价库）+ 自定义 image URL 重写
- 数据请求：原生 fetch + 小型封装（或 TanStack Query；MVP 可先不用）

## 11. 验收标准与测试计划（映射 AGE-12 / AGE-13）

### 11.1 后端验收（最小必要集）

- WS：连接后能收到 TaskProgress 推送；断线不会导致任务失败；多客户端订阅不影响任务
- assets：插图可访问；`..` 等路径穿越被拒绝
- download：zip 可下载、可解压；markdown 引用的图片路径正确且图片存在

### 11.2 前端验收（最小必要集）

- 单页闭环跑通：`image_dir` → 创建任务 → 进度 → 预览（含图）→ 下载 zip
- WS 连接失败/断开：自动降级轮询，终态正确

### 11.3 自动化测试建议

- 后端（pytest）：
  - WS 基本联通（建立连接后能收到至少 1 条 progress）
  - assets 防路径穿越
  - download zip 内容校验
- 前端（可选 Playwright）：覆盖主路径“创建→完成→预览→下载”
