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

# DocRestore Frontend Documentation

React SPA responsible for user interaction, task creation, real-time progress display, result preview and download.

## 1. Documentation Index

| Document | Content |
|---|---|
| [Tech Stack](tech-stack.md) | Vite / React 19 / TypeScript strict / zod / code quality constraints |
| [Feature Design](features.md) | Component structure, state model, progress acquisition strategy, image rewriting, i18n |

## 2. Feature Overview

- **Three image source options**: Local upload (chunked session) / server directory browsing / server file aggregation (symlinks)
- **Per-task configuration override**: OCR engine (PaddleOCR / DeepSeek-OCR-2), LLM model, PII redaction
- **Real-time progress**: WebSocket preferred, automatic fallback to 1-second polling on failure; stops connection on terminal state
- **Result preview**: Markdown rendering (react-markdown + remark-gfm + rehype-raw), supports multi-document sub-document switching
- **Manual refinement**: `PUT /results/{index}` to save revisions; supports original text comparison
- **Task management**: History list (pagination / status filtering), cancel, retry, delete
- **i18n**: zh-CN / zh-TW / en, language switch persisted to `localStorage`
- **Authentication**: Bearer Token (optional, requests pass through when server-side is not configured), configured via sidebar `TokenSettings`

## 3. Trust Boundary & Runtime Model

The frontend is deployed on the same machine or within the same intranet as the backend by default (see [../deployment.md](../deployment.md)).

Key constraints are enforced by the backend (the frontend does not provide fallback guarantees):
- `image_dir` must be an existing directory containing only supported image extensions (jpg/jpeg/png, case-insensitive)
- The `assets` endpoint prevents path traversal: only `document.md` and `images/**` within the task's `output_dir` subtree are accessible
- `/filesystem/dirs` lists directories only, not files; upload sessions are isolated in temporary directories

## 4. Engineering Structure

```
frontend/
├── src/
│   ├── api/              # HTTP/WS client + zod schema + authentication
│   ├── components/       # UI components (Sidebar/SourcePicker/TaskForm/TaskDetail ...)
│   ├── features/task/    # Task domain modules (useTaskRunner/useFileUpload/markdown rewriting)
│   ├── hooks/            # Custom Hooks (useTheme ...)
│   ├── i18n/             # Context + zh-CN / zh-TW / en language packs
│   ├── App.tsx           # Root component
│   └── main.tsx          # Entry point
├── tests/                # vitest + @testing-library/react
├── public/               # Static assets
└── vite.config.ts        # dev proxy: /api → 127.0.0.1:8000 (including WS)
```

## 5. Backend API Integration

All REST / WebSocket contracts are defined by the backend in [backend/api.md](../backend/api.md). The frontend `src/api/schemas.ts` uses zod for runtime validation:

- `POST /tasks` -- Create task
- `GET /tasks` -- Task list (pagination + status filtering)
- `GET /tasks/{id}` / `POST /tasks/{id}/cancel` / `DELETE /tasks/{id}` / `POST /tasks/{id}/retry`
- `GET /tasks/{id}/results` -- Multi-document result array
- `PUT /tasks/{id}/results/{index}` -- Save manual refinement
- `GET /tasks/{id}/download` -- Download zip
- `GET /tasks/{id}/assets/{asset_path:path}` -- Fetch image/sub-document assets
- `POST /uploads` → `POST /uploads/{sid}/files` → `POST /uploads/{sid}/complete` -- Chunked upload
- `POST /sources/server` -- Server file aggregation
- `GET /filesystem/dirs` -- Server directory browsing
- `WS /tasks/{id}/progress` -- Progress push

## 6. Related Documentation

- [Tech Stack & Code Quality Constraints](tech-stack.md)
- [Feature Design & Component Structure](features.md)
- [Backend API Contract](../backend/api.md)
- [Deployment & Startup](../deployment.md)
