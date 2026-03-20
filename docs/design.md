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

# DocRestore 架构设计文档

## 1. 项目概述

DocRestore 将一组连续拍摄的文档照片还原为格式化的 Markdown 文档。

核心挑战：
- 相邻照片有重叠区域，OCR 输出包含重复/循环内容
- 需要智能去重并拼接为连续文档
- 保持原文档的结构（标题、列表、代码块、插图）
- 模型常驻 GPU，支持连续 OCR 多张照片

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      对外 API 层                         │
│              FastAPI REST（WebSocket 后续迭代）            │
│  POST /tasks  GET /tasks/{id}  GET /tasks/{id}/result    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    Pipeline 编排层                        │
│                  TaskManager + Pipeline                   │
│         负责任务生命周期、阶段调度、进度上报               │
└──────┬──────────┬──────────┬──────────┬─────────────────┘
       │          │          │          │
┌──────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼──────┐
│  OCR 层  │ │ 去重层 │ │ LLM层  │ │ 输出层   │
│ OCREngine│ │ Dedup  │ │Refiner │ │ Renderer │
│ (抽象)   │ │ Merger │ │ (抽象) │ │          │
└──────────┘ └────────┘ └────────┘ └──────────┘
```

四层结构，每层职责单一，层间通过数据对象传递，不跨层调用。

### 2.1 层次职责

| 层 | 职责 | 输入 | 输出 |
|---|---|---|---|
| API 层 | 接收请求、任务管理、进度推送 | HTTP/WS 请求 | JSON 响应 |
| Pipeline 层 | 编排处理流程、调度各阶段 | 任务配置 + 图片路径列表 | PipelineResult |
| 处理层（OCR/去重/LLM/输出） | 各自独立的处理逻辑 | 上一阶段的数据对象 | 本阶段的数据对象 |

### 2.2 工程评估

这个四层架构是**刚刚好**的：
- 不是过度工程：每层都有明确的、不可合并的职责。OCR 和 LLM 是不同的模型调用，去重是纯算法逻辑，输出是格式渲染——它们天然分离
- 不是欠工程：如果把 OCR+去重+LLM 混在一起，调试和替换都会很痛苦。抽象 OCR/LLM 接口是必要的，因为明确要求可配置后端
- API 层是必要的：要求"对外暴露的 API 要包含输入输出状态查询"，且要能接入 RAG 等框架

## 3. 数据流

```
照片列表 [img1, img2, ..., imgN]
    │
    ▼ ① OCR 阶段（逐张，模型常驻）
[PageOCR(img1, ...), PageOCR(img2, ...), ...]
    │  → 每张照片产出独立目录 {output_dir}/{stem}_OCR/
    │    ├── result_ori.mmd          # 原始输出（含 grounding 标签）
    │    ├── result.mmd              # grounding 已解析、图片已裁剪替换的 markdown
    │    ├── result_with_boxes.jpg   # 布局可视化
    │    └── images/                 # 裁剪的插图（0.jpg, 1.jpg, ...）
    │
    ▼ ② 清洗阶段（逐页，基于 result.mmd）
[PageOCR(img1, cleaned_md1, ...), ...]    # 页内循环段落去重、乱码移除、空行规范化
    │
    ▼ ③ 去重合并阶段（相邻页滚动合并）
MergedDocument(markdown, images, gaps=[])
    │  → 滚动合并：先合并 1+2 → temp，再合并 temp+3 → temp，...
    │
    ▼ ④ 分段
list[Segment]       # 优先按标题切分，过长时按空行二次切分
    │
    ▼ ⑤ LLM 精修阶段（逐段）
list[RefinedResult] # 格式修正、缺口检测、结构还原
    │
    ▼ ⑥ 重组阶段
MergedDocument      # 拼接各段精修结果，收集所有 Gap，更新 images
    │
    ▼ ⑦ 输出阶段
PipelineResult      # output_path + markdown + images + gaps
```

> **debug 模式**（`PipelineConfig.debug=True`，默认开启）：Pipeline 在各阶段将中间产物落盘到 `output_dir/debug/`，包括每页清洗结果（`{stem}_cleaned.md`）、合并原文（`merged_raw.md`）、每段 LLM 输入/输出（`segments/{i}_input.md`、`segments/{i}_output.md`）、重组结果（`reassembled.md`）。

### 3.1 关键数据对象

```python
@dataclass
class PageOCR:
    """单张照片的 OCR 结果"""
    image_path: Path          # 原始照片路径
    image_size: tuple[int, int]  # 原图尺寸 (width, height)
    raw_text: str             # OCR 原始输出（含 grounding 标签）
    cleaned_text: str         # 清洗后的文本
    regions: list[Region]     # grounding 检测到的图片区域
    output_dir: Path          # 该页的输出目录（含 raw/cleaned text + images/）
    has_eos: bool             # 是否正常结束（无 eos 可能是循环截断）

@dataclass
class Region:
    """图片中检测到的区域（插图/截图）"""
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    label: str                        # 区域描述
    cropped_path: Path | None         # 裁剪后保存路径

@dataclass
class MergedDocument:
    """合并后的完整文档"""
    markdown: str             # 合并去重后的 markdown
    images: list[Region]      # 所有插图
    gaps: list[Gap]           # 检测到的内容缺口

@dataclass
class Gap:
    """内容缺口，需要重新 OCR"""
    after_image: str          # 缺口出现在哪张照片之后（文件名，如 DSC04657.jpg）
    context_before: str       # 缺口前的上下文
    context_after: str        # 缺口后的上下文

@dataclass
class MergeResult:
    """两页合并的中间结果"""
    text: str                 # 合并后的文本
    overlap_lines: int        # 检测到的重叠行数
    similarity: float         # 重叠区域的匹配相似度

@dataclass
class Segment:
    """文档分段（供 LLM 逐段精修）"""
    text: str                 # 分段文本
    start_line: int           # 在原文中的起始行号
    end_line: int             # 在原文中的结束行号

@dataclass
class RefineContext:
    """LLM 精修上下文"""
    segment_index: int        # 当前段序号（从 1 开始）
    total_segments: int       # 总段数
    overlap_before: str       # 与前段重叠的上下文（空字符串表示第一段）
    overlap_after: str        # 与后段重叠的上下文（空字符串表示最后一段）

@dataclass
class RefinedResult:
    """LLM 精修单段的结果"""
    markdown: str             # 精修后的 markdown
    gaps: list[Gap]           # 检测到的缺口

@dataclass
class PipelineResult:
    """Pipeline 处理的最终结果"""
    output_path: Path         # 最终 .md 文件路径
    markdown: str             # 最终 markdown 内容
    images: list[Region]      # 所有插图
    gaps: list[Gap]           # 检测到的内容缺口

@dataclass
class TaskProgress:
    """任务进度"""
    stage: str                # ocr / clean / merge / refine / render
    current: int              # 当前进度
    total: int                # 总数
    percent: float            # 百分比
    message: str              # 进度描述
```

## 4. 各层详细设计

### 4.1 OCR 层

```python
class OCREngine(Protocol):
    """OCR 引擎抽象接口"""
    async def initialize(self) -> None: ...
    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR: ...
    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        """逐张调用 ocr()，每完成一张回调 on_progress(current, total)"""
        ...
    async def shutdown(self) -> None: ...
    @property
    def is_ready(self) -> bool: ...

class DeepSeekOCR2Engine:
    """DeepSeek-OCR-2 实现，基于 vLLM AsyncLLMEngine"""
    # 模型常驻 GPU，initialize() 时加载，shutdown() 时释放
    # ocr() 完整流程：
    #   1. vLLM 推理 → 原始输出（含 grounding 标签）
    #   2. 保存 result_ori.mmd
    #   3. 解析 grounding 标签（re_match）、裁剪插图、替换为 ![](images/N.jpg)
    #   4. 保存 result.mmd + images/ + result_with_boxes.jpg
    #   5. 构造 PageOCR 返回
    # 内置 NoRepeatNGramLogitsProcessor 抑制循环输出
    # grounding 解析/裁剪逻辑从官方脚本提取集成（eval → ast.literal_eval）
```

关键设计决策：
- 使用 Free OCR + grounding 组合 prompt：`<image>\nFree OCR.\n<|grounding|>Convert the document to markdown.`
  - Free OCR 提供高质量文本识别，grounding 提供 bounding box 坐标
  - 输出包含 `<|ref|>...<|/ref|><|det|>...<|/det|>` 标签，可定位插图区域
  - 经手动验证，此组合 prompt 效果最佳
- vLLM AsyncLLMEngine 常驻，避免每张照片重新加载模型
- NoRepeatNGramLogitsProcessor（ngram_size=20, window_size=90）在生成时抑制循环
- 图片预处理：动态分辨率，全局视图 1024x1024 + 最多 6 个 768x768 局部裁切

### 4.2 清洗层

OCR 引擎输出的 result.mmd 已完成 grounding 解析和图片裁剪替换，但仍可能包含：
- 循环重复段落（NoRepeatNGram 未完全抑制的）
- 乱码或无意义 token

清洗策略：
1. 基于文本相似度的段落级去重（同一页内的循环输出）
2. 移除明显乱码（连续非 CJK/ASCII 字符超过阈值）
3. 空行规范化（压缩多余空行）

### 4.3 去重合并层

这是核心难点。相邻照片的重叠内容需要识别和合并。

```
照片 A 的 OCR 输出：          照片 B 的 OCR 输出：
┌──────────────────┐         ┌──────────────────┐
│ A 独有内容        │         │                  │
│                  │         │ 重叠区域（与 A 尾部│
│ 重叠区域（与 B 头部│         │ 相同的内容）      │
│ 相同的内容）      │         │                  │
└──────────────────┘         │ B 独有内容        │
                             │                  │
                             └──────────────────┘
合并结果：
┌──────────────────┐
│ A 独有内容        │
│ 重叠区域（保留一份）│
│ B 独有内容        │
└──────────────────┘
```

去重算法：
1. 将 A 和 B 的文本按行分割
2. 从 A 的尾部和 B 的头部寻找最长公共子序列（LCS）
3. 使用 difflib.SequenceMatcher 做模糊匹配（OCR 可能有微小差异）
4. 找到重叠区域后，保留 A 的版本（或取两者中更完整的）
5. 拼接：A 独有 + 重叠（一份）+ B 独有

滚动合并流程：
```python
async def merge_pages(pages: list[PageOCR]) -> MergedDocument:
    """滚动合并所有页面"""
    merged = pages[0].cleaned_text
    for i in range(1, len(pages)):
        merged = deduplicate_and_merge(merged, pages[i].cleaned_text)
    return MergedDocument(markdown=merged, ...)
```

去重合并后，在每页边界插入 `<!-- page: {filename} -->` 标记，供 LLM 定位缺口位置。
图片引用在合并阶段重写为 `{stem}_OCR/images/N.jpg`，输出阶段再统一重写为 `images/{stem}_N.jpg`。

### 4.4 LLM 精修层

```python
class LLMRefiner(Protocol):
    """LLM 精修接口"""
    async def refine(self, raw_markdown: str, context: RefineContext) -> RefinedResult: ...

class CloudLLMRefiner:
    """云端 API 实现，基于 litellm 统一调用（Claude/GPT/GLM 等）"""
    # 通过 litellm.acompletion() 调用，切换 provider 只需改 model 名

class LocalLLMRefiner:
    """本地 LLM 实现（预留）"""
    # 未来扩展
```

LLM 精修的职责：
1. **格式修正**：修复 OCR 导致的 markdown 格式错误（未闭合的代码块、错误的标题层级等）
2. **缺口检测**：检查文本是否有不连贯的跳跃，标记为 Gap
3. **结构还原**：识别列表、表格、代码块等结构，确保 markdown 语法正确
4. **不改写内容**：LLM 只修格式，不改原文含义

分段策略：
- 26 张照片的文本量可能很大，需要分段送入 LLM
- 优先在 markdown 标题处分段（`# `, `## ` 等），保持语义完整性
- 无标题时退回到空行处分段
- 每段保留前后 `segment_overlap_lines`（默认 5）行上下文重叠，overlap 直接拼入 Segment.text
- 单段不超过 `max_chars_per_segment`（默认 18000 字符）

缺口处理（MVP 仅标记，不自动补充）：
- LLM 检测到不连贯的跳跃 → 在输出中插入 `<!-- GAP: after_image=..., context_before="...", context_after="..." -->`
- Pipeline 收集所有 Gap 标记，写入 `PipelineResult.gaps`
- 用户可根据 Gap 信息手动补拍或调整后重新处理
- 自动重新 OCR 补充功能留到后续迭代

### 4.5 输出层

```python
class Renderer:
    """将精修后的文档渲染为最终输出"""
    async def render(self, document: MergedDocument, output_dir: Path) -> Path:
        # 1. 从各页 {stem}_OCR/images/ 汇总插图到最终 images/ 目录
        #    重命名为 {page_index}_{region_index}.jpg 避免冲突
        # 2. 同步更新 markdown 中的图片引用路径（![](images/0.jpg) → ![](images/3_0.jpg)）
        # 3. 写入最终 .md 文件
        ...
```

## 5. API 设计

### 5.1 REST API

```
POST   /api/v1/tasks
  Body: {
    "image_dir": "/path/to/photos",
    "output_dir": "/path/to/output",       // 可选
    "llm": {                                // 可选，请求级 LLM 配置覆盖
      "model": "anthropic/claude-sonnet-4-20250514",
      "api_base": "https://your-proxy.com/v1",
      "api_key": "sk-xxx",
      "max_chars_per_segment": 18000
    }
  }
  Response: { "task_id": "uuid", "status": "pending" }
  说明：POST 立即返回，任务通过 asyncio.create_task() 后台执行。
        不传 llm 则使用服务启动时的默认 PipelineConfig。

GET    /api/v1/tasks/{task_id}
  Response: { "task_id": "...", "status": "processing", "progress": {...} }

GET    /api/v1/tasks/{task_id}/result
  Response: { "task_id": "...", "output_path": "...", "markdown": "..." }

DELETE /api/v1/tasks/{task_id}          // 后续迭代，当前未实现
```

### 5.2 WebSocket 进度推送（后续迭代）

```
WS /api/v1/tasks/{task_id}/progress

消息格式：
{
  "stage": "ocr",           // ocr | clean | merge | refine | render
  "current": 5,
  "total": 26,
  "percent": 19.2,
  "message": "正在 OCR 第 5 张照片..."
}
```

### 5.3 编程接口（供 RAG 等框架集成）

```python
from docrestore import Pipeline, PipelineConfig
from docrestore.pipeline.config import LLMConfig
from docrestore.ocr.deepseek_ocr2 import DeepSeekOCR2Engine

config = PipelineConfig(
    llm=LLMConfig(model="anthropic/claude-sonnet-4-20250514"),
    # llm=LLMConfig(
    #     model="anthropic/claude-sonnet-4-20250514",
    #     api_base="https://your-proxy.com/v1",  # 中转站用户取消注释
    #     api_key="sk-xxx",                       # 或从环境变量读取
    # ),
)

pipeline = Pipeline(config)
pipeline.set_ocr_engine(DeepSeekOCR2Engine(config.ocr))  # 注入 OCR 引擎
await pipeline.initialize()  # 加载 OCR 模型

result = await pipeline.process(
    image_dir=Path("/path/to/photos"),
    output_dir=Path("/path/to/output"),
    on_progress=None,  # 可选：Callable[[TaskProgress], None] 进度回调
    llm_override=None, # 可选：dict，请求级覆盖 LLM 配置
)
# result: PipelineResult
# result.output_path  — 最终 .md 文件路径
# result.markdown     — 最终 markdown 内容
# result.images       — 所有插图 (list[Region])
# result.gaps         — 检测到的内容缺口 (list[Gap])

await pipeline.shutdown()  # 释放 GPU
```

## 6. 目录结构

```
docrestore/
├── CLAUDE.md
├── pyproject.toml
├── src/
│   └── docrestore/
│       ├── __init__.py           # 公开 API：Pipeline, PipelineConfig
│       ├── api/
│       │   ├── __init__.py
│       │   ├── app.py            # FastAPI 应用
│       │   ├── routes.py         # REST 路由
│       │   └── schemas.py        # Pydantic 请求/响应模型（CreateTaskRequest, LLMConfigRequest 等）
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── pipeline.py       # Pipeline 编排
│       │   ├── task_manager.py   # 任务生命周期管理
│       │   └── config.py         # PipelineConfig
│       ├── ocr/
│       │   ├── __init__.py
│       │   ├── base.py           # OCREngine Protocol
│       │   ├── deepseek_ocr2.py  # DeepSeek-OCR-2 实现
│       │   ├── preprocessor.py   # 图片预处理（动态分辨率、tile 切分）
│       │   └── ngram_filter.py   # NoRepeatNGram 处理器
│       ├── processing/
│       │   ├── __init__.py
│       │   ├── cleaner.py        # OCR 输出清洗（页内去重、乱码、空行）
│       │   └── dedup.py          # 相邻页去重合并
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── base.py           # LLMRefiner Protocol
│       │   ├── cloud.py          # 云端 API 实现
│       │   ├── prompts.py        # LLM prompt 模板
│       │   └── segmenter.py      # 文档分段器
│       ├── output/
│       │   ├── __init__.py
│       │   └── renderer.py       # Markdown 渲染输出
│       └── models.py             # 数据对象（PageOCR, Region, Gap 等）
├── tests/
│   ├── ...
│   └── support/
│       └── ocr_engine.py         # TestOnlyOCREngine（测试专用：读取 *_OCR/ 目录，无 GPU 也能跑端到端）
└── docs/
    ├── design.md                 # 本文档
    └── progress.md               # 开发进度
```

## 7. 配置

配置通过嵌套 dataclass 管理，详见 [models.md](modules/models.md)：

```python
@dataclass
class PipelineConfig:
    ocr: OCRConfig = field(default_factory=OCRConfig)
    dedup: DedupConfig = field(default_factory=DedupConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
```

各子配置的默认值：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `ocr.engine` | `"deepseek-ocr-2"` | OCR 引擎标识 |
| `ocr.model_path` | `"models/DeepSeek-OCR-2"` | 本地模型路径 |
| `ocr.gpu_memory_utilization` | `0.75` | GPU 显存占用比例 |
| `ocr.max_model_len` | `8192` | 最大序列长度 |
| `ocr.max_tokens` | `8192` | 单次推理最大生成 token 数 |
| `ocr.prompt` | `"<image>\nFree OCR.\n<\|grounding\|>..."` | OCR prompt |
| `ocr.base_size` | `1024` | 全局视图尺寸 |
| `ocr.crop_size` | `768` | 局部 tile 尺寸 |
| `ocr.max_crops` | `6` | 最多 tile 数 |
| `ocr.min_crops` | `2` | 最少 tile 数 |
| `ocr.ngram_size` | `20` | 循环抑制 ngram 大小 |
| `ocr.ngram_window_size` | `90` | 循环抑制滑动窗口 |
| `ocr.ngram_whitelist_token_ids` | `{128821, 128822}` | ngram 白名单 token（grounding 标签） |
| `dedup.similarity_threshold` | `0.8` | 行级模糊匹配阈值 |
| `dedup.overlap_context_lines` | `3` | 保留给 LLM 的重叠上下文行数 |
| `dedup.search_ratio` | `0.7` | 取 A 尾部和 B 头部的比例 |
| `llm.model` | `""` | litellm 模型名 |
| `llm.api_base` | `""` | 自定义 API 地址（中转站/本地），为空用默认 |
| `llm.api_key` | `""` | 为空则由 litellm 从环境变量读取 |
| `llm.max_chars_per_segment` | `18000` | 分段字符上限 |
| `llm.segment_overlap_lines` | `5` | 相邻段重叠行数 |
| `llm.max_retries` | `2` | LLM 调用失败最大重试次数 |
| `output.image_format` | `"jpg"` | 输出图片格式 |
| `output.image_quality` | `95` | 输出图片质量 |
| `debug` | `True` | 是否保存中间产物到 `output_dir/debug/` |

## 8. 关键技术决策

### 8.1 为什么用 Free OCR + grounding 组合 prompt？

使用 `<image>\nFree OCR.\n<|grounding|>Convert the document to markdown.` 而非单独的 grounding 或 Free OCR：
- Free OCR 部分提供高质量的文本识别输出
- grounding 部分输出 bounding box 坐标，可精确定位插图区域并裁剪
- 组合使用兼得两者优势，经实测效果最佳
- 纯 grounding 模式丢失了 Free OCR 的文本质量优势
- 纯 Free OCR 模式丢失了空间定位信息

### 8.2 为什么滚动合并而非全部 OCR 完再合并？

滚动合并（1+2 → temp, temp+3 → temp, ...）的优势：
- 内存友好：不需要同时持有所有页面的文本
- 可以尽早发现缺口并触发重新 OCR
- 中间结果可保存，支持断点续传

但实际实现中，由于 26 张照片的文本量不大（估计总共 ~50KB 文本），
全部 OCR 完再合并也完全可行。滚动合并的主要价值在于**动态保存中间结果**
（如 DSC04657.md + DSC04658.md → temp.md），便于调试和检查。

**结论**：采用滚动合并，但不是为了省内存，而是为了中间产物可检查。

### 8.3 去重算法选择

考虑过的方案：
1. **精确行匹配**：太脆弱，OCR 对同一行文字可能输出微小差异
2. **TF-IDF + 余弦相似度**：太重，且对短文本效果差
3. **difflib.SequenceMatcher**：刚好——支持模糊匹配，标准库自带，对行级文本效果好

选择方案 3。具体做法：
- 取 A 尾部 30% 和 B 头部 30% 的行
- 用 SequenceMatcher 找最长匹配块
- 匹配比例 > 0.8 视为重叠

### 8.4 LLM 分段策略

文档可能很长，需要分段送入 LLM。分段策略基于语义边界而非固定 token 数：

1. **优先按标题分段**：扫描 markdown 标题（`# `, `## ` 等），在标题处切分
2. **退回空行分段**：如果两个标题之间的内容仍然过长，在空行处二次切分
3. **上下文重叠**：相邻段之间保留前后各 `segment_overlap_lines`（默认 5）行重叠上下文，直接拼入 Segment.text
4. **单段上限**：每段不超过 `max_chars_per_segment`（默认 18000 字符），MVP 不做 context_window → chars 的自动推导

这比固定 token 数分段更合理，因为保持了语义完整性——一个章节的内容不会被从中间截断。

## 9. 错误处理与重试

### 9.1 OCR 失败
- 单张照片 OCR 失败：记录错误，标记该页为 Gap，继续处理后续照片
- 模型崩溃：尝试重新初始化 vLLM 引擎，最多重试 2 次

### 9.2 LLM 缺口检测
- MVP 仅标记：LLM 检测到缺口 → 插入 GAP 注释 → Pipeline 收集到 `PipelineResult.gaps`
- 用户可根据 Gap 信息手动补拍或调整后重新处理
- 自动重新 OCR 补充功能留到后续迭代

### 9.3 去重失败
- 相邻页找不到重叠 → 可能照片之间确实没有重叠，直接拼接
- 重叠匹配度过低 → 保留两份，让 LLM 判断

## 10. 第一版范围（MVP）

包含：
- [x] DeepSeek-OCR-2 引擎集成（Free OCR + grounding 组合模式）
- [x] OCR 输出清洗（循环去重、grounding 标签解析）
- [x] 相邻页去重合并
- [x] 云端 LLM 精修（格式修正 + 缺口检测）
- [x] Markdown + 插图输出
- [x] Python API（Pipeline 接口）
- [x] 基本 REST API

不包含（后续迭代）：
- [ ] IDE 代码照片 → 源文件
- [ ] WebSocket 实时进度
- [ ] Web 前端
- [ ] 本地 LLM 支持
- [ ] PDF 输入支持
- [ ] 批量任务队列
- [ ] 隐私内容去除（人名、公司名等敏感信息脱敏）
- [ ] 多文档自动聚类（轻量 OCR 提取头部标题 → 按标题分组 + 连续性约束 → 分目录后逐个跑 Pipeline）

## 11. 依赖

```
# 核心
vllm >= 0.8.5
torch >= 2.6.0
transformers
Pillow

# API
fastapi
uvicorn
pydantic

# LLM 调用
litellm          # 统一 LLM 接口（Claude/GPT/GLM 等），自动管理 API key

# 异步 IO
aiofiles         # 异步文件读写

# 工具
difflib        # 标准库，去重匹配

# 测试
pytest
pytest-asyncio
httpx            # 异步 HTTP 客户端（FastAPI TestClient 依赖）
```