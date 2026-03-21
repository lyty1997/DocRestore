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

# WebSocket 进度推送（AGE-12）详细设计

## 1. 职责

为任务处理提供实时进度推送能力，供前端（AGE-13）优先使用 WebSocket 获取进度，并在 WS 不可用时降级为 REST 轮询。

## 2. 需求追踪（Linear）

- AGE-12：WebSocket 实时进度推送（Backlog / Medium）
  - https://linear.app/agentic-graph-rag/issue/AGE-12/websocket-实时进度推送

## 3. 接口契约

### 3.1 WebSocket 路径

- `WS /api/v1/tasks/{task_id}/progress`

### 3.2 消息格式（MVP）

MVP 仅推送裸 `TaskProgress` JSON（与 `docs/design.md §5.2` 一致）：

```json
{
  "stage": "ocr",
  "current": 5,
  "total": 26,
  "percent": 19.2,
  "message": "正在 OCR 第 5 张照片..."
}
```

字段含义：同 `src/docrestore/models.py::TaskProgress`。

### 3.3 语义约定

- 只承诺推送“最新进度快照”，允许合并/丢弃中间值（避免慢客户端背压拖垮任务）
- 终态（completed/failed）不强依赖 WS 消息；前端仍可在关键时机调用 `GET /api/v1/tasks/{task_id}` 确认终态
- MVP 不做鉴权；但实现需避免资源泄漏与路径穿越（本模块不涉及文件访问）

## 4. 服务端实现方案

### 4.1 总体结构

- API 层新增 WS handler（FastAPI WebSocket 路由）
- Pipeline/TaskManager 增加“进度订阅/广播”机制：`task_id -> subscribers`
- `Pipeline.process(... on_progress=...)` 仍是单一真实进度来源

### 4.2 订阅与广播（TaskManager 内部）

#### 4.2.1 数据结构（建议）

- `self._subscribers: dict[str, set[Subscriber]]`
- `Subscriber` 表示一个 WS 连接对应的队列与清理函数

#### 4.2.2 背压策略

- 每个 subscriber 使用 `asyncio.Queue(maxsize=1)`：
  - 新进度到来时，若队列满则丢弃旧值再放入（只保留最新）
  - 目的：避免慢客户端拖垮任务；也避免消息无限堆积造成内存泄漏

#### 4.2.3 广播触发点

- `run_task()` 内部 `on_progress(progress)`：
  1) 更新 `task.progress`
  2) 同步/异步广播给所有 subscriber（按背压策略投递）

并发安全：
- `_tasks` 的状态更新已有 `asyncio.Lock`；订阅集合的增删也需在同一把锁或独立锁下完成

### 4.3 WebSocket handler 行为

- 连接建立后：
  - 校验 `task_id` 存在；不存在则 close（或返回 404 语义）
  - 注册 subscriber
  - 立即推送一次“当前快照”（如果 task.progress 已有值）以减少首帧延迟
- 消息循环：
  - 从 subscriber 队列取最新进度，`send_json(progress)`
  - 连接断开/异常：注销 subscriber
- 资源清理：
  - 断开时必须注销 subscriber
  - 任务进入终态（completed/failed）后，可选择主动给所有 subscriber 发送最后一次快照并关闭连接（MVP 可选）

## 5. 测试计划（后端最小必要集）

- pytest：
  - 建立 WS 连接后能收到至少 1 条 progress（可用测试专用 OCR 引擎/短任务）
  - 断开连接后 subscriber 能被清理（可通过内部计数或黑盒行为验证）

## 6. 与前端的对接要点

- 前端应实现 WS→轮询降级：
  - WS 握手失败、超时、异常断开 → 启动 `GET /tasks/{task_id}` 轮询
- 前端渲染进度字段来自 TaskProgress（stage/current/total/percent/message）

