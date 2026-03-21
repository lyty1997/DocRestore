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

# 结果资源访问与下载（AGE-13）详细设计

## 1. 职责

为 Web 前端的“结果预览（含插图）+ 下载 zip”补齐后端能力：

- assets：受限访问该任务 output_dir 下的 `document.md` 与 `images/**`
- download：一键下载 zip（包含 `document.md` + `images/`）

## 2. 需求追踪（Linear）

- AGE-13：Web 前端界面（Backlog / Low）
  - https://linear.app/agentic-graph-rag/issue/AGE-13/web-前端界面

## 3. 背景：为什么需要这两个接口

当前 `GET /api/v1/tasks/{task_id}/result` 只返回 markdown 文本。

- markdown 内图片引用为相对路径 `images/<stem>_<idx>.jpg`（由 `Renderer` 输出阶段生成）
- 浏览器无法直接访问后端文件系统路径，因此必须通过后端提供“受限静态文件访问”

## 4. assets 接口设计（受限静态资源访问）

### 4.1 路径

- `GET /api/v1/tasks/{task_id}/assets/{asset_path:path}`

### 4.2 允许访问的资源（MVP）

- `document.md`
- `images/**`

### 4.3 安全要求（必须）

- 防路径穿越：`asset_path` 必须是相对路径，禁止包含 `..`
- 防软链接穿越：解析后的真实路径必须位于该任务 `output_dir` 下
- 建议：只允许白名单前缀（`document.md`、`images/`），其余返回 404

### 4.4 响应

- 成功：返回文件内容（`FileResponse`）
- 不存在/越权：404（避免泄露文件系统结构）

## 5. download 接口设计（zip 打包下载）

### 5.1 路径

- `GET /api/v1/tasks/{task_id}/download`

### 5.2 zip 内容结构

```
document.md
images/
  <stem>_<idx>.jpg
```

### 5.3 关键约束

- zip 内 `document.md` 中图片引用必须是相对路径 `images/...`
  - 当前 `Renderer` 输出满足该约束

### 5.4 打包策略（实现建议）

- 在内存中生成 zip（适合小结果）；或用临时文件流式返回
- 必须使用 `tempfile` 管理临时文件（资源安全）
- 文件枚举只从 `output_dir/document.md` 与 `output_dir/images/` 获取

## 6. 与现有模块的对接点

- `TaskManager`：需要能拿到 task.result.output_path（即 `output_dir/document.md`）
- `Renderer`：负责生成 `document.md` 与 `images/` 目录（本接口只做“读取与打包”，不改写内容）

## 7. 测试计划（后端最小必要集）

- assets：
  - `images/<file>` 可访问
  - `../` 等路径穿越被拒绝
- download：
  - 响应是 zip
  - zip 内包含 `document.md` 与 images/
  - markdown 引用的图片在 zip 内存在

