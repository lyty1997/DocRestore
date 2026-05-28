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

# DocRestore Documentation / DocRestore 文档

DocRestore converts consecutively photographed document images into formatted Markdown documents (with illustrations).

DocRestore 将连续拍摄的文档照片还原为格式化的 Markdown 文档（含插图）。

## Documentation / 文档

| Language | Entry Point |
|----------|-------------|
| [English](en/README.md) | Full English documentation |
| [中文](zh/README.md) | 完整中文文档 |

## Current Source Of Truth / 当前事实源

- Current implementation docs live under `docs/zh/` and are the primary source for active development.
- `docs/zh/progress.md` is the iteration log, not a design contract. Before coding, prefer `architecture.md` and the relevant module docs.
- AGE documents and `references/` are historical design notes unless the Chinese index explicitly links them as current.

- 当前实现文档以 `docs/zh/` 为主；开发前优先查看 `architecture.md` 和对应模块文档。
- `docs/zh/progress.md` 是迭代流水，不是接口契约。旧进度条目可能已被后续实现覆盖。
- AGE 文档和 `references/` 目录默认视为历史设计记录；只有中文索引明确列为当前事实源时才按当前实现使用。

## Directory Structure / 目录结构

```
docs/
├── README.md          # This file (bilingual index)
├── en/                # English documentation
│   ├── README.md
│   ├── architecture.md
│   ├── deployment.md
│   ├── backend/
│   │   ├── README.md
│   │   ├── data-models.md
│   │   ├── ocr.md
│   │   ├── processing.md
│   │   ├── llm.md
│   │   ├── privacy.md
│   │   ├── pipeline.md
│   │   ├── api.md
│   │   ├── performance_toolkit.md
│   │   └── references/
│   │       ├── deepseek-ocr2.md
│   │       ├── streaming-pipeline.md
│   │       └── pipeline-parallel.md
│   └── frontend/
│       ├── README.md
│       ├── tech-stack.md
│       └── features.md
└── zh/                # 中文文档
    ├── README.md
    ├── architecture.md
    ├── deployment.md
    ├── known-issues.md
    ├── progress.md
    ├── backend/
    │   ├── README.md
    │   ├── data-models.md
    │   ├── ocr.md
    │   ├── processing.md
    │   ├── llm.md
    │   ├── privacy.md
    │   ├── pipeline.md
    │   ├── api.md
    │   ├── performance_toolkit.md
    │   └── references/
    │       ├── deepseek-ocr2.md
    │       ├── streaming-pipeline.md
    │       └── pipeline-parallel.md
    └── frontend/
        ├── README.md
        ├── tech-stack.md
        └── features.md
```
