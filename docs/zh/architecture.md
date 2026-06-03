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
- 代码模式需要从 IDE 屏幕照片恢复源文件，保留来源页、行号范围和诊断信息，便于人工审查
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

文档模式是**流式生产者/消费者**：OCR 边产出、LLM 边消费，一个目录视为一篇文档。

```
文档模式（_stream_pipeline）：
  ① OCR 生产者：逐页 OCR → ② 清洗 → ③ 可选 regex PII → 入 page_queue
                                     ∥（并发）
  ④ 流式消费者：逐页增量合并 → ⑤ 按 L* 切段 → ⑥ LLM 段级精修
       （⑦ 满 5 页异步取 PII lexicon）
  收齐后终结化：⑧ 重组 → ⑨ 缺口补充(可选) → ⑩ 整篇精修(可选)
              → ⑪ 程序化去重兜底 → ⑫ 输出 → 单个 PipelineResult

代码模式分支（_code_pipeline）：
  ① OCR text_lines → ② IDE 布局/行号列识别 → ③ 代码栏组装
    → ④ 跨页按路径/文件名分组为 SourceFile → ⑤ LLM 字符级精修/修复
    → ⑥ 轻量诊断 → ⑦ 输出 files/、files-index.json 和兼容 Markdown

PPT 模式分支（_ppt_pipeline，设计中，见 ppt-mode.md）：
  ① S2 透视矫正(逐页前处理) → ② VL-1.6 doc_parser 版面识别 + 化学结构裁图
    → ③ 单页保序组装 → ④ 多页按原序合并(不去重) → ⑤ 可选 LLM 轻润色
    → ⑥ 输出 document.md、images/
```

详细说明（文档模式）：
- ① OCR：逐张照片 OCR，生成每页 `{stem}_OCR/` 目录；OCR 串行受 `gpu_lock` 保护
- ② 清洗：页内去重、乱码/空行修复
- ③ PII regex（可选）：生产阶段逐页 `redact_regex_only`（手机/邮箱/身份证/银行卡）先行脱敏
- ④ 增量合并：`IncrementalMerger.add_page()` 逐页滚动合并去重，插入 `<!-- page: ... -->` 边界标记
- ⑤ 流式切段：`StreamSegmentExtractor` 按 `RateController` 运行时自适应段长 L* 从增长中的文本切段
- ⑥ LLM 精修：逐段修复 markdown 结构，解析 Gap 标记，检测模型截断（`finish_reason == "length"` 或启发式行数比），命中 `LLMCache` 跳过；失败回退原文
- ⑦ PII 实体检测（可选）：满 5 页后异步 `detect_pii_entities()` 取 `EntityLexicon`，供缺口补充 re-OCR 片段复用
- ⑧ 重组：`_reassemble()` 拼接各段结果
- ⑨ 缺口补充（可选）：`OCREngine.reocr_page()` re-OCR + `LLMRefiner.fill_gap()`，带 GPU 锁与单 gap 异常降级
- ⑩ 整篇精修（可选）：全文最终精修，再次 `parse_gaps()`
- ⑪ 程序化去重兜底：0 LLM 成本删除重复 HTML 表 / H2 章节 / 代码块视觉行号 / 残留 UI 噪音
- ⑫ 输出：`Renderer` 汇总插图复制/重命名，写入 `output_dir/document.md`
- 代码模式输出：`render_code_files()` 写出 `output_dir/files/**`、`files-index.json` 和 `document.md`；`files-index.json` 是前端 CodeViewer 的文件列表、来源页、质量 flags 与诊断事实源

## 4. 目录结构

```
docrestore/
├── backend/docrestore/
│   ├── api/              # FastAPI 应用与路由（REST + WebSocket + 文件上传）
│   ├── pipeline/         # Pipeline 编排与调度
│   ├── ocr/              # OCR 引擎（子进程 worker + EngineManager 按需切换）
│   ├── processing/       # 清洗、去重、IDE 布局、代码组装与诊断
│   ├── privacy/          # PII 脱敏
│   ├── llm/              # LLM 精修（云端/本地）与代码精修/修复
│   ├── persistence/      # SQLite 任务持久化
│   ├── output/           # Markdown 渲染与代码模式文件输出
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
- 流式按标题/空行切段，段长 L* 由 `RateController` 运行时自适应
- 相邻段保留 backward overlap 提供上下文（拼入段文本，由 LLM 精修时去重）
- 支持云端（litellm）和本地（OpenAI 兼容 API：vLLM / ollama / llama.cpp）两种 provider
- 截断双层检测：模型 `finish_reason` + 输出/输入行数比启发式阈值（`LLMConfig.truncation_*`）
- 代码 refine 模式对大 SourceFile 按行数/字符数自动切块，单个 chunk 失败只回退该 chunk；rewrite 模式不自动切块

### 5.4 并发模型
- GPU 串行（`asyncio.Lock` 保护 OCR 调用 + 引擎切换）
- `EngineManager.switch_lock` 防止并发切换，等待当前 OCR 操作释放 `gpu_lock` 后再切换引擎
- 无组级并发（单任务独占 GPU）；任务级并发由 TaskManager 控制
- **流式并行已落地**：`process_many` 内 OCR 生产者与 LLM 消费者并发；`process_tree` 多子目录用最长目录 warmup cold start 后并发，共享一个 `RateController`。设计反转由来见 `docs/zh/backend/references/streaming-pipeline.md`，事实源以 `pipeline/` 代码和 `backend/pipeline.md` 为准

## 6. 扩展性设计

### 6.1 可替换组件
- OCR 引擎：实现 `OCREngine` Protocol
- LLM 精修：实现 `LLMRefiner` Protocol
- PII 脱敏：实现 `PIIRedactor` 接口

### 6.2 代码模式的 OCR 契约
- 代码模式不绑定具体 OCR provider，不应在 API 或配置层强制切换到 PaddleOCR。
- 代码模式只依赖抽象产物 `PageOCR.text_lines`：任意 OCR 引擎只要填充行级
  `bbox/text/score`，即可接入 IDE 布局分析链路。
- 当前 OCR 引擎未提供 `text_lines` 时，代码模式应明确失败并提示能力缺失，
  而不是静默跳过页面或退化为文档模式。

### 6.3 当前边界与未来扩展
- 代码模式已支持 IDE 代码照片 → 源文件、来源图片联动、轻量诊断和单文件编辑保存；仍可继续增强函数级切块、项目级依赖图和成熟代码编辑器组件
- **PPT 还原模式（设计中）**：屏摄幻灯片 → 保序 `document.md`（文字 + 公式 LaTeX + 化学结构裁图），第三消费者分支 `_ppt_pipeline` 与文档/代码模式互斥三选一；S1 设计见 [ppt-mode.md](ppt-mode.md)，OpenSpec 提案 `openspec/changes/add-ppt-restore-mode/`，子任务树 AGE-83
- PDF 输入支持
- 前端多文档结果展示已落地基础导航；后续可补真实 fixture 的端到端视觉验证

## 7. 相关文档

- [后端文档索引](backend/README.md)
- [前端文档索引](frontend/README.md)
- [部署指南](deployment.md)
- [开发进度](progress.md)
