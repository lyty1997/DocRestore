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

# LLM 精修层（llm/）

## 1. 职责

对去重合并后的文档进行格式修复、缺口检测和结构还原。基于 litellm 统一调用云端 LLM，不改写原文内容含义。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `llm/base.py` | `LLMRefiner` Protocol + `RefineContext` 定义 |
| `llm/cloud.py` | 云端 API 实现（基于 litellm） |
| `llm/prompts.py` | prompt 模板 + GAP 解析 |
| `llm/segmenter.py` | 文档分段器 |

## 3. 对外接口

### 3.1 LLMRefiner Protocol（llm/base.py）

Pipeline 通过此接口调用 LLM 层。

```python
@dataclass
class RefineContext:
    """精修上下文"""
    segment_index: int                 # 当前段序号（从 1 开始）
    total_segments: int                # 总段数
    overlap_before: str                # 与前段重叠的上下文（空字符串表示第一段）
    overlap_after: str                 # 与后段重叠的上下文（空字符串表示最后一段）

class LLMRefiner(Protocol):
    async def refine(
        self, raw_markdown: str, context: RefineContext
    ) -> RefinedResult:
        """
        精修单段：修复格式 + 检测缺口 + 还原结构，不改写内容含义。
        返回 RefinedResult(markdown, gaps)
        """
        ...
```

**调用约定**：
- 输入：单段 markdown 文本 + 上下文信息
- 输出：`RefinedResult`（精修后的 markdown + 检测到的 Gap 列表）
- 无需 `initialize()` / `shutdown()`——litellm 无状态
- 重试由 litellm 内置机制处理

### 3.2 DocumentSegmenter（llm/segmenter.py）

Pipeline 在调用 LLMRefiner 之前，先用分段器将长文档切分。

```python
@dataclass
class Segment:
    text: str
    start_line: int
    end_line: int

class DocumentSegmenter:
    """将长文档按语义边界分段，供 LLM 逐段精修"""

    def __init__(
        self,
        max_chars_per_segment: int = 12000,
        overlap_lines: int = 5,
    ) -> None: ...

    def segment(self, markdown: str) -> list[Segment]:
        """
        分段策略：
        1. 优先在标题处切分（# / ## / ### 等）
        2. 过长则在空行处二次切分
        3. 每段保留前后 overlap_lines 行重叠，重叠区域用标记包裹：
           <!-- overlap-start --> ... <!-- overlap-end -->
           Pipeline._reassemble() 依据此标记裁剪重叠，不依赖行号
        4. 单段不超过 max_chars_per_segment（默认 12000，可通过 LLMConfig 覆盖）
           MVP 不做 context_window → chars 的自动推导，后续根据实际效果调整
        """
```

**调用约定**：
- 输入：完整 markdown 文本
- 输出：`Segment` 列表，每段含文本和行号范围，重叠区域已用 `<!-- overlap-start/end -->` 标记
- `overlap_lines` 对应 `LLMConfig.segment_overlap_lines`
- `max_chars_per_segment` 默认 12000，用户可通过 `LLMConfig.max_chars_per_segment` 覆盖

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `models.py` | `RefinedResult`, `Gap` |
| `pipeline/config.py` | `LLMConfig` |

不依赖 OCR 层、处理层或输出层。

## 5. 内部实现

### 5.1 CloudLLMRefiner（llm/cloud.py）

```python
class CloudLLMRefiner:
    """通过 litellm 调用云端 LLM（Claude/GPT/GLM 等）"""

    def __init__(self, config: LLMConfig) -> None: ...

    async def refine(
        self, raw_markdown: str, context: RefineContext
    ) -> RefinedResult:
        """
        1. build_refine_prompt(raw_markdown, context) → messages
        2. litellm.acompletion(model, api_base, api_key, messages)
        3. parse_gaps(response) → (cleaned_markdown, gaps)
        4. 返回 RefinedResult
        """
```

LiteLLM 的优势：
- 统一接口：`anthropic/claude-sonnet-4-20250514`、`openai/glm-5`、`openai/gpt-4o` 等，只改 model 名
- API key：可在 config 中指定，也可由 litellm 从 `ANTHROPIC_API_KEY` 等环境变量自动读取
- api_base：支持中转站、本地 vLLM 等自定义地址
- 内置重试、超时、异步支持（`acompletion`）

### 5.2 Prompt 模板（llm/prompts.py）

```python
REFINE_SYSTEM_PROMPT = """你是一个文档格式修复助手。规则：
1. 只修复格式，不改变原文内容含义
2. 修复未闭合的代码块、错误的标题层级、损坏的列表和表格
3. <!-- overlap-start/end --> 之间是照片衔接处，检查连贯性
4. <!-- page: DSC04654.jpg --> 是页边界标记，标识该段文本来自哪张照片
5. 发现内容跳跃则插入 <!-- GAP: after_image=DSC04657.jpg, context_before="前文最后一句", context_after="后文第一句" -->
   其中 after_image 取跳跃处前方最近的 <!-- page: ... --> 中的文件名
6. 保留所有 <!-- page: ... --> 标记，不要删除
7. 输出纯 markdown，不要添加解释"""

REFINE_USER_TEMPLATE = """请修复以下 OCR 产出的 markdown（第 {segment_index}/{total_segments} 段）：
{overlap_before}
---正文开始---
{raw_markdown}
---正文结束---
{overlap_after}"""

def build_refine_prompt(
    raw_markdown: str, context: RefineContext
) -> list[dict[str, str]]:
    """构造 [system, user] messages 列表"""

def parse_gaps(refined_markdown: str) -> tuple[str, list[Gap]]:
    """
    从 LLM 输出中提取 GAP 标记并转为 Gap 对象。
    解析 <!-- GAP: after_image=..., context_before="...", context_after="..." -->
    返回 (清理掉 GAP 标记的 markdown, Gap 列表)

    容错策略：正则尽力匹配，字段缺失或格式错误的标记直接忽略，
    只收集能成功解析的 Gap 对象，不报错、不中断流程。
    """
```

## 6. 数据流

```
MergedDocument.markdown
    │
    ▼ DocumentSegmenter.segment()
[Segment(text, start_line, end_line), ...]
    │
    ▼ LLMRefiner.refine(segment.text, context) × N 段
[RefinedResult(markdown, gaps), ...]
    │
    ▼ Pipeline 拼接各段
refined_markdown + all_gaps
```