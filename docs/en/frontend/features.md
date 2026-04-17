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

# Frontend Feature Design

## 1. Core User Flow

1. Select an image source via `SourcePicker` (one of three options):
   - **Local upload**: `FileUploader` drag-and-drop/file selection -> chunked upload session -> `UploadPreviewPanel` for preview and removal -> obtain temporary `image_dir` on completion
   - **Server browsing**: `DirectoryPicker` browses server directories to directly select an existing `image_dir`
   - **Server file aggregation**: Multi-select server files -> `POST /sources/server` -> symlinked temporary directory
2. `TaskForm` to fill in `output_dir` + optional advanced configuration (LLM model / OCR engine / PII settings) -> create task; optionally click **Preload Engine** to trigger backend OCR engine switching ahead of submission
3. `TaskProgress` displays real-time progress (WebSocket preferred, polling fallback); `SourceImagePanel` synchronously shows the source image currently being processed
4. `TaskDetail` summary: `TaskResult` renders Markdown (with images) + multi-document navigation (single-document tasks have only one entry)
5. Download (`/download` zip) / save manual refinement (`PUT /results/{index}`) / retry / delete

## 2. State Management

### 2.1 Task State
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

### 2.2 Progress State
```typescript
interface TaskProgress {
  stage: string        // ocr/clean/merge/refine/render
  current: number
  total: number
  percent: number
  message: string
}
```

### 2.3 WebSocket State
```typescript
type WSState = "connecting" | "open" | "closed" | "error"

interface ProgressState {
  wsState: WSState
  pollingEnabled: boolean
}
```

## 3. Progress Acquisition Strategy

### 3.1 WebSocket First
- Establish `WS /api/v1/tasks/{task_id}/progress` immediately after task creation
- On successful connection: receive real-time progress pushes
- On connection failure/disconnect: automatic fallback to polling

### 3.2 Polling Fallback
- Polling interval: 1 second
- Calls `GET /api/v1/tasks/{task_id}` to retrieve status
- Stops polling upon reaching a terminal state (completed/failed)

### 3.3 Result Retrieval
- After task completion, calls `GET /api/v1/tasks/{task_id}/result`
- Retrieves markdown content and output path

## 4. Component Structure

```
App
├── Sidebar
│   ├── SidebarTaskList      # Task history list (/tasks pagination)
│   └── TokenSettings        # Auth token configuration (local storage)
├── SourcePicker             # Source selection: upload / server directory / server files
│   ├── FileUploader         # Chunked upload session (/uploads series)
│   ├── UploadPreviewPanel   # Upload preview, single file deletion
│   └── DirectoryPicker      # Server directory browsing (/filesystem/dirs)
├── TaskForm                 # Form (output_dir, LLM/OCR/PII overrides)
├── TaskDetail               # Detail view for created tasks
│   ├── TaskProgress         # Progress display (WS + polling fallback)
│   ├── SourceImagePanel     # Source image viewer (/source-images)
│   └── TaskResult           # Sub-document tabs + Markdown rendering + refinement/download
├── BackToTopButton          # Helper for long Markdown content
└── ConfirmDialog            # Delete/cancel confirmation
```

i18n: `src/i18n/context.tsx` provides the `useTranslation()` Hook; language packs are split into zh-CN / zh-TW / en, and the selection is persisted to `localStorage` on switch.

## 5. Image Reference Rewriting

Image references in Markdown need to be rewritten to assets API paths, compatible with multi-document subdirectories:

- Single document: `images/xxx.jpg` -> `/api/v1/tasks/{task_id}/assets/images/xxx.jpg`
- Multi-document: `images/xxx.jpg` within a sub-document -> `/api/v1/tasks/{task_id}/assets/{doc_dir}/images/xxx.jpg`

Implementation: react-markdown custom `img` component that constructs a prefix using the current result's `doc_dir`.

## 5.5 OCR Engine Warmup

The **Preload Engine** button at the top of `TaskForm` lets the user move the model/GPU into place before submitting a task, avoiding a cold-start wait on the first image.

| State | Trigger | Button label | Side text |
|---|---|---|---|
| `idle` | initial / dropdown changed | "Preload Engine" | -- |
| `warming` | POST `/ocr/warmup` returned `accepted` / `switching` | "Loading..." | -- |
| `ready` | warmup returned `ready` or `/ocr/status` poll matched | "Preload Engine" (disabled) | "Ready" (green) |
| `error` | warmup call threw | "Preload Engine" | "Load Failed" (red) |

Implementation notes (`TaskForm.tsx`):
- One-shot `/ocr/status` query on mount: when the active model/GPU matches the form selection and `is_ready` is true, jump straight to `ready`.
- Switching the OCR engine or GPU dropdown resets `engineStatus` back to `idle`.
- Entering `warming` starts a 3 s `/ocr/status` poll, stops as soon as the target matches and `is_ready`; the timer is hard-capped at 60 s as a fail-safe.
- A `useRef<setInterval>` clears the timer on unmount.

## 6. Download Functionality

Clicking the "Download Result" button:
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

## 7. Error Handling

### 7.1 Network Errors
- API call failure: display error message, allow retry
- WebSocket disconnect: automatic fallback to polling

### 7.2 Task Failure
- Display error summary
- Optionally expand detailed traceback (collapsible panel)

## 8. Resource Cleanup

The following must be cleaned up on component unmount:
- WebSocket connections: `ws.close()`
- Polling timers: `clearInterval()`
- AbortController: `abort()`

## 9. Related Documentation

- [Tech Stack](tech-stack.md)
- [Backend API](../backend/api.md)
