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

# 前端功能设计

## 1. 核心用户路径

1. 通过 `SourcePicker` 选择图片来源（三选一）：
   - **本地上传**：`FileUploader` 拖拽/选择文件 → 分片上传会话 → `UploadPreviewPanel` 预览与删改 → 完成后获取临时 `image_dir`
   - **服务器浏览**：`DirectoryPicker` 浏览服务器目录，直接选中现有 `image_dir`
   - **服务器文件聚合**：多选服务器文件 → `POST /sources/server` → 符号链接临时目录
2. `TaskForm` 填写 `output_dir` + 可选高级配置（LLM 模型 / OCR 引擎 / PII 设置）→ 创建任务
3. `TaskProgress` 展示实时进度（WebSocket 优先，轮询降级）；`SourceImagePanel` 同步展示当前处理的源图片
4. `TaskDetail` 汇总：`TaskResult` 展示 Markdown（含图）+ 多子文档导航（单文档任务只有一项）
5. 下载（`/download` zip）/ 人工精修保存（`PUT /results/{index}`）/ 重试 / 删除

## 2. 状态管理

### 2.1 任务状态
```typescript
type TaskStatus = "idle" | "pending" | "processing" | "completed" | "failed"

interface TaskState {
  taskId: string | null
  status: TaskStatus
  progress?: TaskProgress
  resultMarkdown?: string
  error?: string
}
```

### 2.2 进度状态
```typescript
interface TaskProgress {
  stage: string        // ocr/clean/merge/refine/render
  current: number
  total: number
  percent: number
  message: string
}
```

### 2.3 WebSocket 状态
```typescript
type WSState = "connecting" | "open" | "closed" | "error"

interface ProgressState {
  wsState: WSState
  pollingEnabled: boolean
}
```

## 3. 进度获取策略

### 3.1 WebSocket 优先
- 创建任务后立即建立 `WS /api/v1/tasks/{task_id}/progress`
- 连接成功：接收实时进度推送
- 连接失败/断开：自动降级到轮询

### 3.2 轮询降级
- 轮询间隔：1 秒
- 调用 `GET /api/v1/tasks/{task_id}` 获取状态
- 到达终态（completed/failed）后停止轮询

### 3.3 结果获取
- 任务完成后调用 `GET /api/v1/tasks/{task_id}/result`
- 获取 markdown 内容和输出路径

## 4. 组件结构

```
App
├── Sidebar
│   ├── SidebarTaskList      # 任务历史列表（/tasks 分页）
│   └── TokenSettings        # 认证 token 配置（本地存储）
├── SourcePicker             # 来源选择：上传 / 服务器目录 / 服务器文件
│   ├── FileUploader         # 分片上传会话（/uploads 系列）
│   ├── UploadPreviewPanel   # 上传预览、单文件删除
│   └── DirectoryPicker      # 服务器目录浏览（/filesystem/dirs）
├── TaskForm                 # 表单（output_dir、LLM/OCR/PII 覆盖）
├── TaskDetail               # 已创建任务的详情视图
│   ├── TaskProgress         # 进度展示（WS + 轮询降级）
│   ├── SourceImagePanel     # 源图片查看器（/source-images）
│   └── TaskResult           # 子文档 tab + Markdown 渲染 + 精修/下载
├── BackToTopButton          # 长 Markdown 辅助
└── ConfirmDialog            # 删除/取消确认
```

i18n：`src/i18n/context.tsx` 提供 `useTranslation()` Hook；语言包分 zh-CN / zh-TW / en 三份，切换时写入 `localStorage`。

## 5. 图片引用重写

Markdown 中的图片引用需要重写为 assets API 路径，兼容多文档子目录：

- 单文档：`images/xxx.jpg` → `/api/v1/tasks/{task_id}/assets/images/xxx.jpg`
- 多文档：子文档中的 `images/xxx.jpg` → `/api/v1/tasks/{task_id}/assets/{doc_dir}/images/xxx.jpg`

实现方式：react-markdown 自定义 `img` 组件，通过当前 result 的 `doc_dir` 拼接 prefix。

## 6. 下载功能

点击"下载结果"按钮：
```typescript
const downloadResult = async (taskId: string) => {
  const response = await fetch(`/api/v1/tasks/${taskId}/download`)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `docrestore-${taskId}.zip`
  a.click()
  URL.revokeObjectURL(url)
}
```

## 7. 错误处理

### 7.1 网络错误
- API 调用失败：显示错误提示，允许重试
- WebSocket 断开：自动降级轮询

### 7.2 任务失败
- 显示错误摘要
- 可选展开详细 traceback（折叠面板）

## 8. 资源清理

组件卸载时必须清理：
- WebSocket 连接：`ws.close()`
- 轮询定时器：`clearInterval()`
- AbortController：`abort()`

## 9. 相关文档

- [技术栈](tech-stack.md)
- [后端 API](../backend/api.md)
