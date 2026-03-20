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

# DocRestore MVP 模块设计总览

本文档是模块设计的索引，各模块的详细设计见 `docs/modules/` 下的独立文档。

## 1. 模块结构

```
src/docrestore/
├── models.py              # 数据对象
├── pipeline/
│   ├── config.py          # PipelineConfig
│   ├── pipeline.py        # Pipeline 编排
│   └── task_manager.py    # 任务生命周期
├── ocr/
│   ├── base.py            # OCREngine Protocol
│   ├── deepseek_ocr2.py   # DeepSeek-OCR-2 实现
│   ├── preprocessor.py    # 图片预处理
│   └── ngram_filter.py    # NoRepeatNGram 处理器
├── processing/
│   ├── cleaner.py         # OCR 输出清洗（页内去重、乱码、空行）
│   └── dedup.py           # 相邻页去重合并
├── llm/
│   ├── base.py            # LLMRefiner Protocol
│   ├── cloud.py           # 云端 API 实现
│   ├── prompts.py         # prompt 模板
│   └── segmenter.py       # 文档分段器
├── output/
│   └── renderer.py        # Markdown 渲染输出
└── api/
    ├── app.py             # FastAPI 应用
    └── routes.py          # REST 路由
```

## 2. 模块文档索引

| 模块 | 文档 | 核心接口 |
|---|---|---|
| 数据对象 + 配置 | [models.md](modules/models.md) | `PageOCR`, `MergedDocument`, `Gap`, `Segment`, `RefineContext`, `PipelineResult`, `PipelineConfig` |
| OCR 层 | [ocr.md](modules/ocr.md) | `OCREngine.ocr()`, `OCREngine.ocr_batch()` |
| 处理层 | [processing.md](modules/processing.md) | `OCRCleaner.clean()`, `PageDeduplicator.merge_all_pages()` |
| LLM 精修层 | [llm.md](modules/llm.md) | `LLMRefiner.refine()`, `DocumentSegmenter.segment()` |
| 输出层 | [output.md](modules/output.md) | `Renderer.render()` |
| Pipeline 编排层 | [pipeline.md](modules/pipeline.md) | `Pipeline.process()`, `TaskManager` |
| API 层 | [api.md](modules/api.md) | `POST/GET/DELETE /api/v1/tasks` |

## 3. 模块间对接 API 总览

```
Pipeline.process() 内部调用链：

  ┌─ OCREngine.ocr_batch(image_paths, output_dir, on_progress) → list[PageOCR]
  │      输入：list[Path]（排序后的照片路径）+ Path（任务输出目录）
  │      输出：list[PageOCR]（raw_text 已填充，cleaned_text 为空）
  │      每张照片产出 output_dir/{image_stem}_OCR/ 子目录
  │
  ├─ OCRCleaner.clean(page) → PageOCR     （逐页调用，async）
  │      输入：PageOCR（cleaned_text 为空）
  │      输出：同一 PageOCR（cleaned_text 已填充）
  │
  ├─ PageDeduplicator.merge_all_pages(pages) → MergedDocument
  │      输入：list[PageOCR]（cleaned_text 已填充）
  │      输出：MergedDocument（markdown + images，gaps 为空）
  │      合并时插入 <!-- page: {filename} --> 页边界标记
  │      图片引用重写为 {stem}_OCR/images/N.jpg
  │
  ├─ DocumentSegmenter.segment(markdown) → list[Segment]
  │      输入：合并后的完整 markdown
  │      输出：分段列表（含重叠上下文）
  │
  ├─ LLMRefiner.refine(raw_markdown, context) → RefinedResult     （逐段调用）
  │      输入：单段 markdown 文本 + RefineContext
  │      输出：RefinedResult（精修 markdown + gaps）
  │
  ├─ Pipeline._reassemble(refined_results, merged_doc) → MergedDocument
  │      输入：list[RefinedResult] + 原 MergedDocument
  │      输出：MergedDocument（markdown 替换为精修版，gaps 从各段收集汇总）
  │
  └─ Renderer.render(document, output_dir) → Path
         输入：MergedDocument（精修后）+ 输出目录
         输出：document.md 路径

Pipeline.process() 返回 PipelineResult（output_path + markdown + images + gaps）
```

## 4. 模块依赖关系

```
api/routes.py
    → pipeline/task_manager.py
        → pipeline/pipeline.py
            → ocr/base.py (Protocol)
            → ocr/deepseek_ocr2.py (实现)
                → ocr/preprocessor.py
                → ocr/ngram_filter.py
            → processing/cleaner.py
            → processing/dedup.py
            → llm/base.py (Protocol)
            → llm/cloud.py (实现)
                → llm/prompts.py
                → llm/segmenter.py
            → output/renderer.py
    → models.py (所有模块共享)
    → pipeline/config.py (所有模块共享)
```

依赖规则：
- `models.py` 和 `pipeline/config.py` 是公共基础，不依赖其他模块
- 处理层各模块（ocr / processing / llm / output）之间不互相依赖
- 只有 `pipeline.py` 知道所有处理层模块的存在并编排它们
- API 层只依赖 Pipeline 层，不直接依赖处理层

## 5. 数据流总览

```
照片列表 [img1, img2, ..., imgN]
    │
    ▼ ① OCR（ocr_batch）
list[PageOCR]  →  每张照片产出 {stem}_OCR/ 目录
    │
    ▼ ② 清洗（await clean × N）
list[PageOCR]  →  cleaned_text 已填充
    │
    ▼ ③ 去重合并（merge_all_pages）
MergedDocument  →  完整 markdown + overlap 标注 + 页边界标记 + 图片引用重写
    │
    ▼ ④ 分段（segment）
list[Segment]
    │
    ▼ ⑤ LLM 精修（refine × M 段）
list[RefinedResult]  →  精修 markdown + gaps
    │
    ▼ ⑥ 重组（_reassemble）
MergedDocument  →  拼接精修结果，收集所有 Gap
    │
    ▼ ⑦ 输出（render）
document.md + images/
    │
    ▼ 返回
PipelineResult(output_path, markdown, images, gaps)
```