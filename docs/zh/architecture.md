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

# DocRestore 系统架构

## 1. 项目概述

DocRestore 将连续拍摄的文档照片还原为格式化的 Markdown 文档（含插图）。

核心挑战：
- 相邻照片存在重叠，OCR 输出会包含重复/循环内容，需要算法级去重并拼接为连续正文
- 需要尽可能保持原文档结构（标题、列表、表格、代码块、插图引用）
- OCR 模型常驻 GPU，支持连续处理多张照片；LLM 精修可配置云端/本地提供方

## 2. 系统架构

```
┌───────────────────────────────────────────────────────────┐
│                        Web 前端层                         │
│       React SPA（上传、进度展示、结果预览、任务历史）      │
└───────────────────────┬───────────────────────────────────┘
                        │ HTTP + WebSocket（Bearer Token）
┌───────────────────────▼───────────────────────────────────┐
│                         对外 API 层                        │
│       FastAPI REST + WebSocket + 分片上传 + Token 鉴权      │
│  /tasks  /uploads  /sources  /filesystem  /results  ...    │
└───────────────────────┬───────────────────────────────────┘
                        │
┌───────────────────────▼───────────────────────────────────┐
│                      Pipeline 编排层                       │
│    TaskManager（SQLite 持久化）+ Pipeline（调度/进度）     │
└─────────┬───────────┬───────────┬───────────┬─────────────┘
          │           │           │           │
┌─────────▼───┐ ┌─────▼─────┐ ┌───▼────────┐ ┌────▼─────┐ ┌────▼─────┐
│   OCR 层     │ │ 清洗/去重  │ │ PII/隐私层  │ │  LLM 层   │ │  输出层   │
│ OCREngine(*) │ │ Cleaner+   │ │ Redactor(*) │ │ Refiner(*)│ │ Renderer  │
│ EngineManager│ │ Dedup+Merge│ │（可选）     │ │（可选）   │ │           │
└──────────────┘ └───────────┘ └─────────────┘ └───────────┘ └───────────┘
(* 抽象接口，可替换实现)
```

### 2.1 层次职责

| 层 | 职责 | 输入 | 输出 |
|---|---|---|---|
| Web 前端 | 用户交互、进度展示、结果预览 | 用户操作 | HTTP/WS 请求 |
| API 层 | 接收请求、任务管理、进度推送 | HTTP/WS 请求 | JSON 响应 |
| Pipeline 层 | 编排处理流程、调度各阶段 | 任务配置 + 图片目录 | `PipelineResult` |
| 处理层 | 独立处理逻辑（OCR/清洗/LLM/输出） | 上一阶段数据对象 | 本阶段数据对象 |

### 2.2 工程评估

这个四层架构是**刚刚好**的：
- 不是过度工程：OCR、去重合并、隐私脱敏、LLM 精修、输出渲染在依赖（GPU/云端）与失败模式上完全不同，天然需要隔离
- 不是欠工程：如果把 OCR/去重/LLM/脱敏混在一起，会导致替换后端、调试与回归验证都非常困难
- 抽象 OCR/LLM/隐私接口是必要的：明确要求后端可配置，并需要在失败时可降级

## 3. 数据流

```
① OCR → ② 清洗 → ③ 去重合并 → ④ PII 脱敏(可选)
    → ⑤ 分段 → ⑥ LLM 精修 → ⑦ 重组
    → ⑧ 多文档边界检测(可选) → [每个子文档分别进入以下流程]
    → ⑨ 缺口补充(可选) → ⑩ 整篇精修(可选) → ⑪ 输出
```

详细说明：
- ① OCR：逐张照片 OCR，生成每页 `{stem}_OCR/` 目录
- ② 清洗：页内去重、乱码/空行修复
- ③ 去重合并：相邻页滚动合并，跨页频率过滤（`strip_repeated_lines`）移除侧栏噪声，插入 `<!-- page: ... -->` 边界标记
- ④ PII 脱敏（可选）：结构化正则（手机/邮箱/身份证/银行卡）+ LLM 实体检测，产出 `EntityLexicon` 供 re-OCR 片段复用
- ⑤ 分段：按标题/空行分段，相邻段保留 `overlap_lines` 行上下文
- ⑥ LLM 精修：逐段修复 markdown 结构，解析 Gap 标记，检测模型截断（`finish_reason == "length"` 或启发式行数比）
- ⑦ 重组：拼接段结果，汇总 gaps 与 warnings
- ⑧ 多文档边界检测（可选）：`LLMRefiner.detect_doc_boundaries()` 独立 LLM 调用，将合并文本拆成多个 `PipelineResult`
- ⑨ 缺口补充（可选）：`OCREngine.reocr_page()` re-OCR + `LLMRefiner.fill_gap()`，带 GPU 锁与单 gap 异常降级
- ⑩ 整篇精修（可选）：全文最终精修，再次 `parse_gaps()`
- ⑪ 输出：`Renderer` 汇总插图复制/重命名，按 `doc_dir` 写入（单文档根目录 / 多文档子目录）

## 4. 目录结构

```
docrestore/
├── backend/docrestore/
│   ├── api/              # FastAPI 应用与路由（REST + WebSocket + 文件上传）
│   ├── pipeline/         # Pipeline 编排与调度
│   ├── ocr/              # OCR 引擎（子进程 worker + EngineManager 按需切换）
│   ├── processing/       # 清洗与去重
│   ├── privacy/          # PII 脱敏
│   ├── llm/              # LLM 精修（云端/本地）
│   ├── persistence/      # SQLite 任务持久化
│   ├── output/           # Markdown 渲染输出
│   ├── utils/            # 工具函数
│   └── models.py         # 数据模型
├── frontend/             # React 19 + TypeScript + Vite 前端
├── tests/                # 测试
├── docs/                 # 文档
└── scripts/              # 安装与启动脚本
```

## 5. 关键技术决策

### 5.1 OCR 引擎选择与按需切换
- 主引擎：PaddleOCR（轻量级文档解析）
- 备用引擎：DeepSeek-OCR-2（高精度 grounding OCR，需大显存 GPU）
- **统一子进程架构**：两个引擎均以 subprocess worker 运行在各自的 conda 环境中，通过 JSON Lines 协议通信，后端不直接依赖 torch/vllm
- **EngineManager**：按需切换引擎，同一时刻只有一个引擎占用 GPU。前端选择引擎后，后端自动启动/关闭对应 worker 和 ppocr-server
- OCR Router：统一工厂函数，根据模型标识符创建对应引擎

### 5.2 去重算法
- 使用 `difflib.SequenceMatcher` 做模糊行匹配
- 对 OCR 微小差异更鲁棒，成本适中

### 5.3 LLM 精修策略
- 优先按标题切分，保持语义完整
- 相邻段保留 overlap 提供上下文（拼入 `Segment.text`，由 LLM 精修时去重）
- 支持云端（litellm）和本地（OpenAI 兼容 API：vLLM / ollama / llama.cpp）两种 provider
- 截断双层检测：模型 `finish_reason` + 输出/输入行数比启发式阈值（`LLMConfig.truncation_*`）

### 5.4 多文档边界检测
- 由独立 LLM 调用 `detect_doc_boundaries()` 完成（不与分段精修耦合）
- 合并文本送入 LLM 返回 `list[DocBoundary]`（JSON 容错，解析失败降级为单文档）
- `Pipeline.process_many()` 根据 boundary 切分子文档，每个子文档独立做 gap fill / final refine / render
- 输出目录：单文档写 `output_dir/`，多文档写 `output_dir/{sanitize_dirname(title)}/`；dirname 冲突时 `dedupe_dirnames()` 追加后缀

### 5.5 并发模型
- GPU 串行（`asyncio.Lock` 保护 OCR 调用 + 引擎切换）
- `EngineManager.switch_lock` 防止并发切换，等待当前 OCR 操作释放 `gpu_lock` 后再切换引擎
- 无组级并发（单任务独占 GPU）；任务级并发由 TaskManager 控制
- 流式并行 Pipeline 设计文档见 `docs/backend/references/streaming-pipeline.md`（待实施）

## 6. 扩展性设计

### 6.1 可替换组件
- OCR 引擎：实现 `OCREngine` Protocol
- LLM 精修：实现 `LLMRefiner` Protocol
- PII 脱敏：实现 `PIIRedactor` 接口

### 6.2 未来扩展方向
- IDE 代码照片 → 源文件
- PDF 输入支持
- 流式并行 Pipeline 实施（AGE-16，设计已完成）
- 前端多文档结果展示（AGE-33）

## 7. 相关文档

- [后端文档索引](backend/README.md)
- [前端文档索引](frontend/README.md)
- [部署指南](deployment.md)
- [开发进度](progress.md)
