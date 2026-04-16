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

# DocRestore 文档索引

## 项目概述

DocRestore 将连续拍摄的文档照片还原为格式化的 Markdown 文档（含插图）。

核心能力：
- OCR 识别（DeepSeek-OCR-2 / PaddleOCR）
- 相邻页去重合并
- LLM 精修（云端/本地）
- PII 脱敏（可选）
- Web 界面与 REST API

## 文档结构

```
docs/
├── README.md                    # 本文件（文档索引）
├── architecture.md              # 系统架构总览
├── deployment.md                # 部署与环境配置
├── progress.md                  # 开发进度记录
├── backend/                     # 后端文档
│   ├── README.md                # 后端架构总览
│   ├── data-models.md           # 数据模型与配置
│   ├── ocr.md                   # OCR 层
│   ├── processing.md            # 清洗与去重层
│   ├── llm.md                   # LLM 精修层
│   ├── privacy.md               # PII 脱敏
│   ├── pipeline.md              # Pipeline 编排
│   ├── api.md                   # REST API
│   └── references/              # 参考文档
│       ├── deepseek-ocr2.md     # DeepSeek-OCR-2 参考
│       └── streaming-pipeline.md # 流式并行设计（待实施）
└── frontend/                    # 前端文档
    ├── README.md                # 前端架构总览
    ├── tech-stack.md            # 技术栈与工程规范
    └── features.md              # 功能与交互设计
```

## 快速导航

### 新手入门
1. [系统架构总览](architecture.md) - 了解整体设计
2. [部署指南](deployment.md) - 环境配置与启动
3. [后端架构](backend/README.md) - 后端模块结构
4. [前端架构](frontend/README.md) - 前端技术栈

### 后端开发
- [数据模型](backend/data-models.md) - 核心数据结构与配置
- [OCR 层](backend/ocr.md) - OCR 引擎接口与实现
- [处理层](backend/processing.md) - 清洗与去重算法
- [LLM 层](backend/llm.md) - 精修接口与 prompt
- [Pipeline](backend/pipeline.md) - 流程编排与调度
- [API](backend/api.md) - REST 与 WebSocket 接口

### 前端开发
- [技术栈](frontend/tech-stack.md) - TypeScript/React/Vite 规范
- [功能设计](frontend/features.md) - 交互流程与状态管理

### 参考资料
- [DeepSeek-OCR-2 参考](backend/references/deepseek-ocr2.md)
- [开发进度](progress.md)

## 文档维护规则

- 架构变更：先更新对应模块文档，再修改代码
- 接口变更：必须同步更新 `data-models.md` 和相关模块文档
- 新增功能：在 `progress.md` 记录，完成后更新对应模块文档
- 设计决策：记录在对应模块文档的"设计决策"章节
