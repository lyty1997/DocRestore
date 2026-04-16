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

# DocRestore 后端架构

## 1. 模块结构

```
backend/docrestore/
├── models.py              # 数据对象
├── pipeline/
│   ├── config.py          # PipelineConfig（pydantic BaseModel）
│   ├── pipeline.py        # Pipeline 编排
│   ├── task_manager.py    # 任务生命周期（含 SQLite 持久化）
│   └── scheduler.py       # 全局调度器（GPU Lock）
├── ocr/
│   ├── base.py            # OCREngine Protocol + WorkerBackedOCREngine
│   ├── deepseek_ocr2.py   # DeepSeek-OCR-2 实现
│   ├── paddle_ocr.py      # PaddleOCR 实现
│   ├── router.py          # OCR Router（自动选择引擎）
│   ├── engine_manager.py  # EngineManager（按需切换 + GPU 独占）
│   ├── preprocessor.py    # 图片预处理
│   ├── ngram_filter.py    # NoRepeatNGram 处理器
│   └── column_filter.py   # 侧栏过滤
├── processing/
│   ├── cleaner.py         # OCR 输出清洗
│   ├── dedup.py           # 相邻页去重合并
│   └── segmenter.py       # 文档分段器
├── llm/
│   ├── base.py            # LLMRefiner Protocol + BaseLLMRefiner
│   ├── cloud.py           # CloudLLMRefiner（litellm + PII 实体检测）
│   ├── local.py           # LocalLLMRefiner（OpenAI 兼容本地服务）
│   └── prompts.py         # prompt 模板
├── privacy/
│   ├── patterns.py        # 结构化 PII 正则
│   └── redactor.py        # PIIRedactor + EntityLexicon
├── persistence/
│   └── database.py        # TaskDatabase（SQLite 任务持久化）
├── output/
│   └── renderer.py        # Markdown 渲染输出
├── utils/
│   └── paths.py           # 路径工具
└── api/
    ├── app.py             # FastAPI 应用工厂（含自动环境检测）
    ├── routes.py          # REST 路由 + WebSocket 路由
    ├── upload.py          # 分片上传会话
    ├── auth.py            # Bearer Token 鉴权
    └── schemas.py         # API 请求/响应 Schema
```

## 2. 模块文档索引

| 模块 | 文档 | 核心接口 |
|---|---|---|
| 数据模型与配置 | [data-models.md](data-models.md) | `PageOCR`, `MergedDocument`, `PipelineResult`, `PipelineConfig` |
| OCR 层 | [ocr.md](ocr.md) | `OCREngine.ocr()`, `OCREngine.ocr_batch()`, `EngineManager` |
| 处理层 | [processing.md](processing.md) | `OCRCleaner.clean()`, `PageDeduplicator.merge_all_pages()` |
| LLM 精修层 | [llm.md](llm.md) | `LLMRefiner.refine()`, `fill_gap()`, `final_refine()` |
| PII 脱敏 | [privacy.md](privacy.md) | `PIIRedactor.redact_for_cloud()` |
| Pipeline 编排 | [pipeline.md](pipeline.md) | `Pipeline.process_many()`, `TaskManager` |
| API 层 | [api.md](api.md) | REST + WebSocket + 上传 + 鉴权 |

## 3. 模块依赖关系

```
api/app.py
    ├─ api/auth.py        (Bearer Token 鉴权)
    ├─ api/upload.py      (分片上传会话)
    └─ api/routes.py
        → pipeline/task_manager.py
            → persistence/database.py (SQLite 持久化)
            → pipeline/scheduler.py   (GPU Lock)
            → pipeline/pipeline.py
                → ocr/router.py + ocr/engine_manager.py
                    → ocr/deepseek_ocr2.py
                    → ocr/paddle_ocr.py
                → processing/cleaner.py
                → processing/dedup.py
                → llm/cloud.py / llm/local.py
                → privacy/redactor.py
                → output/renderer.py
        → models.py (所有模块共享)
```

依赖规则：
- `models.py` 和 `pipeline/config.py` 是公共基础，不依赖其他模块
- 处理层各模块（ocr/processing/llm/privacy/output）原则上互不依赖
- 只有 `pipeline.py` 知道所有处理层模块的存在并编排它们
- API 层只依赖 Pipeline 层，不直接依赖处理层

## 4. 数据流总览

```
照片列表 [img1, img2, ..., imgN]
    │
    ▼ 逐张处理（GPU Lock 串行）
    for each image:
      ① OCR（ocr）→ PageOCR
      ② 清洗（clean）→ PageOCR（cleaned_text 已填充）
    │
    ▼ ③ 去重合并（merge_all_pages）→ MergedDocument
    │
    ▼ ④ PII 脱敏（可选）→ MergedDocument（脱敏后）
    │
    ▼ ⑤ 分段（segment）→ list[Segment]
    │
    ▼ ⑥ LLM 精修（refine × M 段）→ list[RefinedResult]
    │
    ▼ ⑦ 重组（_reassemble）→ MergedDocument
    │
    ▼ ⑧ 缺口自动补充（可选）→ MergedDocument
    │
    ▼ ⑨ 整篇精修（可选）→ MergedDocument
    │
    ▼ ⑩ 输出（render）→ document.md
    │
    → PipelineResult
```

## 5. 编程接口

```python
from pathlib import Path
from docrestore import Pipeline, PipelineConfig
from docrestore.pipeline.config import LLMConfig

config = PipelineConfig(
    llm=LLMConfig(model="anthropic/claude-sonnet-4-20250514"),
)

pipeline = Pipeline(config)
await pipeline.initialize()

results = await pipeline.process_many(
    image_dir=Path("/path/to/photos"),
    output_dir=Path("/path/to/output"),
)

# results: list[PipelineResult]（LLM 聚类可能拆成多份）
# results[0].output_path        — .md 文件路径
# results[0].markdown           — markdown 内容
# results[0].images             — 所有插图
# results[0].gaps               — 检测到的内容缺口
# results[0].warnings           — 流程警告
# results[0].redaction_records  — PII 脱敏统计

await pipeline.shutdown()
```

## 6. 错误处理策略

### 6.1 OCR 失败
- 单页失败：记录 warning，允许流程继续
- 引擎崩溃：有限次数重试初始化；失败则任务失败

### 6.2 LLM 精修失败
- 单段失败：回退原文，记录 warning，不中断流程
- 缺口补充失败：降级为"仅标记不补充"

### 6.3 去重失败
- 找不到重叠：直接拼接
- 匹配度过低：保留更多原文，让 LLM 处理

## 7. 相关文档

- [数据模型](data-models.md)
- [OCR 层](ocr.md)
- [处理层](processing.md)
- [LLM 层](llm.md)
- [Pipeline](pipeline.md)
- [API](api.md)
