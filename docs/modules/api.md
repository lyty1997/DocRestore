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

# API 层（api/）

## 1. 职责

对外暴露 REST API，接收任务请求、查询状态、获取结果。只依赖 Pipeline 层，不直接依赖处理层。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `api/app.py` | FastAPI 应用创建、生命周期管理 |
| `api/routes.py` | REST 路由定义 |

## 3. 对外接口

### 3.1 FastAPI 应用（api/app.py）

```python
def create_app(config: PipelineConfig | None = None) -> FastAPI:
    """
    创建 FastAPI 应用。
    - startup: 初始化 Pipeline（加载 OCR 模型）
    - shutdown: 释放资源
    """
    app = FastAPI(title="DocRestore", version="0.1.0")
    app.include_router(router, prefix="/api/v1")
    return app
```

### 3.2 REST 路由（api/routes.py）

#### POST /api/v1/tasks — 创建任务

```python
class CreateTaskRequest(BaseModel):
    image_dir: str                     # 照片目录路径
    output_dir: str | None = None      # 输出目录，为空则自动生成

class TaskResponse(BaseModel):
    task_id: str
    status: str                        # pending / processing / completed / failed
    progress: dict | None = None       # TaskProgress 序列化
    error: str | None = None           # 失败时的完整错误信息（MVP 暴露 traceback）
```

请求示例：
```json
{
  "image_dir": "/mnt/TrueNAS_Share/Linux_SDK/development_guide/",
  "output_dir": "/tmp/docrestore_output"
}
```

响应示例：
```json
{
  "task_id": "a1b2c3d4",
  "status": "pending",
  "progress": null
}
```

#### GET /api/v1/tasks/{task_id} — 查询任务状态

响应示例（处理中）：
```json
{
  "task_id": "a1b2c3d4",
  "status": "processing",
  "progress": {
    "stage": "ocr",
    "current": 5,
    "total": 26,
    "percent": 19.2,
    "message": "正在 OCR 第 5 张照片..."
  }
}
```

#### GET /api/v1/tasks/{task_id}/result — 获取结果

```python
class TaskResultResponse(BaseModel):
    task_id: str
    output_path: str                   # 最终 .md 文件路径
    markdown: str                      # 完整 markdown 文本
```

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `pipeline/task_manager.py` | `TaskManager`, `Task`, `TaskStatus` |
| `pipeline/config.py` | `PipelineConfig` |
| `models.py` | `TaskProgress`, `PipelineResult` |

不直接依赖 OCR 层、处理层、LLM 层或输出层。

## 5. 内部实现

```python
router = APIRouter()

@router.post("/tasks", response_model=TaskResponse)
async def create_task(req: CreateTaskRequest, bg: BackgroundTasks):
    """创建任务 → bg.add_task(task_manager.run_task, task_id)"""

@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """查询任务状态和进度"""

@router.get("/tasks/{task_id}/result", response_model=TaskResultResponse)
async def get_result(task_id: str):
    """获取已完成任务的结果（状态非 completed 返回 404）"""
```

## 6. MVP 不含

- WebSocket 实时进度推送（`/api/v1/tasks/{task_id}/progress`）
- MCP Server（AI agent 集成，如 Claude Desktop / RAG agent 自主调用）
- CreateTaskRequest 配置覆盖（options 字段）
- Web 前端（已在 `docs/frontend-design.md` 定义前端技术规格，待落地实现）
- 认证/鉴权

## 7. 错误响应（MVP）

MVP 阶段返回完整错误信息方便调试：

- 任务失败时 `Task.error` 存完整 traceback
- GET `/api/v1/tasks/{task_id}` 响应中包含 `error` 字段

```json
{
  "task_id": "a1b2c3d4",
  "status": "failed",
  "progress": { "stage": "ocr", "current": 3, "total": 26, "percent": 11.5, "message": "..." },
  "error": "Traceback (most recent call last):\n  ..."
}
```

上线前收紧为结构化错误（错误码 + 用户友好消息），不暴露内部堆栈。