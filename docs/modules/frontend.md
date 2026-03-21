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

# 前端模块（frontend/）详细设计

## 1. 职责

前端模块负责提供本地 Web UI，实现单页闭环：输入图片目录 → 创建任务 → 展示进度（WS 优先、轮询降级）→ 结果预览（含插图）→ 下载 zip。

说明：当前仓库尚无前端工程，本设计文档用于指导 AGE-13 的前端落地实现。

## 2. 需求追踪（Linear）

- AGE-13：Web 前端界面（Backlog / Low）
  - https://linear.app/agentic-graph-rag/issue/AGE-13/web-前端界面
- 关联：AGE-12 WebSocket 实时进度推送（Backlog / Medium）
  - https://linear.app/agentic-graph-rag/issue/AGE-12/websocket-实时进度推送

## 3. 非目标（MVP 不做）

来源：`docs/frontend-design.md` §2。

- 远程部署/公网访问、用户体系、认证鉴权（已建后续 issue：AGE-30）
- 浏览器端目录选择并上传图片（已建后续 issue：AGE-27）
- 多任务并发与任务历史持久化（已建后续 issue：AGE-29）
- 取消任务 / 删除任务 / 重试（已建后续 issue：AGE-28）
- SSE（已建后续 issue：AGE-31）

## 4. 工程形态与目录结构（建议）

前端为独立项目，通过 HTTP/WS 访问后端：

```
frontend/
  package.json
  tsconfig.json
  vite.config.ts
  src/
    main.tsx
    App.tsx
    api/
      client.ts          # fetch + zod 校验
      schemas.ts         # zod schema（TaskResponse/TaskProgress/TaskResultResponse）
    features/
      task/
        useTaskRunner.ts # 任务创建、WS 订阅、轮询降级
        markdown.ts      # markdown 图片 URL 重写
    components/
      TaskForm.tsx
      TaskProgress.tsx
      TaskResult.tsx
```

说明：本节是推荐结构，不要求一次性全实现。MVP 可先在 `App.tsx` 内完成闭环，再逐步拆分。

## 5. 关键接口契约（前端消费视角）

前端仅依赖下列后端接口：

- `POST /api/v1/tasks` 创建任务（body: image_dir/output_dir?/llm?）
- `GET /api/v1/tasks/{task_id}` 查询状态（轮询兜底）
- `GET /api/v1/tasks/{task_id}/result` 获取 markdown
- `WS /api/v1/tasks/{task_id}/progress` 实时进度（AGE-12）
- `GET /api/v1/tasks/{task_id}/assets/{asset_path:path}` 插图/文档资源（AGE-13 需要，后端新增）
- `GET /api/v1/tasks/{task_id}/download` 下载 zip（AGE-13 需要，后端新增）

## 6. 状态机与数据流（MVP）

### 6.1 页面状态机

- idle：未创建任务
- pending/processing：已创建任务，展示进度
- completed：展示结果预览与下载
- failed：展示错误信息

### 6.2 进度获取（WS 优先 + 降级轮询）

1. 创建任务成功后立即尝试建立 WS
2. WS 成功：使用 WS 推送更新 UI
3. WS 失败/断开：启动轮询 `GET /tasks/{task_id}`（间隔 1s，直到终态）
4. 终态后：关闭 WS/停止轮询；completed 时拉取 result

注意：WS 消息仅承诺“最新快照”，终态以 REST 为准。

## 7. Markdown 预览与插图显示

### 7.1 关键约束

- 后端最终输出 markdown 中图片引用是相对路径 `images/<stem>_<idx>.jpg`
- 浏览器无法直接访问后端文件系统路径，因此必须通过 assets 接口取图

### 7.2 图片 URL 重写策略

渲染 markdown 前，将所有 `images/...` 重写为：

`/api/v1/tasks/{task_id}/assets/images/...`

实现可以是：
- markdown AST 层重写（推荐：更稳健）
- 或 string 层替换（MVP 可接受，但要小心误替换）

## 8. 运行时校验与错误处理

- 所有 API 响应在进入 UI 状态前必须做运行时校验（推荐 zod）
- WS 收到的 progress JSON 同样做 schema 校验；失败则记录并触发轮询兜底
- 错误展示：MVP 可折叠展示 traceback（与后端当前返回行为一致），但 UI 默认只展示摘要

## 9. 测试建议（最小必要集）

- 单元测试：
  - markdown 图片 URL 重写
  - WS→轮询降级逻辑（可用 mock WebSocket）
- e2e（可选）：Playwright 跑通“创建→完成→预览→下载”

## 10. 后续迭代（已建 Linear issues）

- AGE-27：浏览器端目录选择与上传接口
- AGE-28：任务取消/删除/重试
- AGE-29：任务历史持久化与任务列表接口
- AGE-30：认证鉴权与错误信息脱敏
- AGE-31：SSE 进度推送（WebSocket 备选）
