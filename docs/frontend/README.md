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

# DocRestore 前端文档

React SPA，负责用户交互、任务创建、实时进度展示、结果预览与下载。

## 1. 文档索引

| 文档 | 内容 |
|---|---|
| [技术栈](tech-stack.md) | Vite / React 19 / TypeScript strict / zod / 代码质量约束 |
| [功能设计](features.md) | 组件结构、状态模型、进度获取策略、图片重写、i18n |

## 2. 功能概览

- **图片来源三选一**：本地上传（分片会话）/ 服务器目录浏览 / 服务器文件聚合（符号链接）
- **任务配置覆盖**：OCR 引擎（PaddleOCR / DeepSeek-OCR-2）、LLM 模型、PII 脱敏
- **实时进度**：WebSocket 优先，失败自动降级 1 秒轮询；终态停止连接
- **结果预览**：Markdown 渲染（react-markdown + remark-gfm + rehype-raw），支持多文档子文档切换
- **人工精修**：`PUT /results/{index}` 保存修订；支持原文对比
- **任务管理**：历史列表（分页 / 状态筛选）、取消、重试、删除
- **i18n**：zh-CN / zh-TW / en，切换持久化到 `localStorage`
- **鉴权**：Bearer Token（可选，服务端未配置时放行），通过侧边栏 `TokenSettings` 配置

## 3. 信任边界与运行模式

前端默认与后端部署在同一机器或内网环境（参见 [../deployment.md](../deployment.md)）。

关键约束由后端保证（前端不做兜底）：
- `image_dir` 必须为已存在的目录，且仅包含受支持的图片后缀（jpg/jpeg/png，大小写兼容）
- `assets` 接口防路径穿越：仅允许访问任务 `output_dir` 子树内的 `document.md` 和 `images/**`
- `/filesystem/dirs` 仅列目录、不列文件；上传会话隔离在临时目录中

## 4. 工程结构

```
frontend/
├── src/
│   ├── api/              # HTTP/WS 客户端 + zod schema + 鉴权
│   ├── components/       # UI 组件（Sidebar/SourcePicker/TaskForm/TaskDetail ...）
│   ├── features/task/    # 任务领域模块（useTaskRunner/useFileUpload/markdown 重写）
│   ├── hooks/            # 自定义 Hook（useTheme ...）
│   ├── i18n/             # Context + zh-CN / zh-TW / en 语言包
│   ├── App.tsx           # 根组件
│   └── main.tsx          # 入口
├── tests/                # vitest + @testing-library/react
├── public/               # 静态资源
└── vite.config.ts        # dev proxy：/api → 127.0.0.1:8000（含 WS）
```

## 5. 后端接口对接

所有 REST / WebSocket 契约由后端侧 [backend/api.md](../backend/api.md) 定义，前端 `src/api/schemas.ts` 用 zod 做运行时校验：

- `POST /tasks` — 创建任务
- `GET /tasks` — 任务列表（分页 + 状态筛选）
- `GET /tasks/{id}` / `POST /tasks/{id}/cancel` / `DELETE /tasks/{id}` / `POST /tasks/{id}/retry`
- `GET /tasks/{id}/results` — 多子文档结果数组
- `PUT /tasks/{id}/results/{index}` — 保存人工精修
- `GET /tasks/{id}/download` — 下载 zip
- `GET /tasks/{id}/assets/{asset_path:path}` — 读取图片/子文档资源
- `POST /uploads` → `POST /uploads/{sid}/files` → `POST /uploads/{sid}/complete` — 分片上传
- `POST /sources/server` — 服务器文件聚合
- `GET /filesystem/dirs` — 服务器目录浏览
- `WS /tasks/{id}/progress` — 进度推送

## 6. 相关文档

- [技术栈与代码质量约束](tech-stack.md)
- [功能设计与组件结构](features.md)
- [后端 API 契约](../backend/api.md)
- [部署与启动](../deployment.md)
