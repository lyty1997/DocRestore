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

LLM 精修层负责对 OCR 合并去重后的 markdown 进行“格式修复 + 结构还原 + 缺口检测”，并提供两类增强能力：

- **缺口自动补充（Gap fill）**：当精修检测到内容跳跃时，结合 re-OCR 结果从原始文本中提取缺失片段并插回。
- **整篇文档级精修（Final refine）**：对分段精修重组后的整篇 markdown 再做一遍跨段去重与全局格式清理。

> **ℹ️ 云端 `detect_pii_entities` 已于 S4 删除（2026-06-15）**：人名/机构名检测改**本地 NER**
> （`privacy/ner.py::PIIGuard.detect_entities`，spaCy，名字不出本机）。Pipeline 不再调用任何
> 云端 PII 实体检测方法；`CloudLLMRefiner.detect_pii_entities`、基类
> `BaseLLMRefiner.detect_pii_entities` 默认实现与 `LLMRefiner` Protocol 中的该方法声明已整链删除。
> 详见 [privacy.md](privacy.md) §10.5 / [pii-local-ner.md](pii-local-ner.md)。

同时支持 **云端与本地两种 provider**：

- 云端：通过 LiteLLM 调用各类云模型（Claude / GPT / GLM 等）。
- 本地：通过 OpenAI 兼容 API（vLLM / ollama / llama.cpp 等）调用本地模型。

> 设计原则：**严禁压缩、概括或改写有效内容**；仅修复格式错误、删除明显重复、插入缺口标记/补充缺口内容。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `llm/base.py` | `LLMRefiner` Protocol + `BaseLLMRefiner` 公共实现（litellm 调用、refine/fill_gap/final_refine） |
| `llm/cloud.py` | `CloudLLMRefiner(BaseLLMRefiner)`（云端 provider 选型标识的薄子类；云端实体检测已 S4 删除，人名/机构名改本地 NER） |
| `llm/local.py` | `LocalLLMRefiner(BaseLLMRefiner)`（本地实现，纯继承基类 refine/fill_gap/final_refine） |
| `llm/prompts.py` | prompt 模板 + GAP 解析（`parse_gaps()` 等） |
| `llm/code_refine.py` | `CodeLLMRefiner`（代码模式字符级精修 / rewrite 模式） |
| `llm/code_repair.py` | `DiagnosticCodeRepairer`（诊断驱动 scoped patch）+ `CodeConsistencyAuditor`（重诊断 + 接受门） |

> 文档分段器 `DocumentSegmenter` 已迁至 `processing/segmenter.py`（见 [处理层文档](processing.md)）。分段不依赖 LLM，属于纯文本处理。

## 3. 对外接口

### 3.1 LLMRefiner Protocol（llm/base.py）

Pipeline 通过此 Protocol 调用 LLM 精修能力。所有方法都是协议成员（基类默认实现），运行时不再做 `hasattr` 能力探测。

```python
class LLMRefiner(Protocol):
    async def refine(
        self, raw_markdown: str, context: RefineContext,
    ) -> RefinedResult: ...

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str: ...

    async def final_refine(self, markdown: str) -> RefinedResult: ...
```

> Protocol 不含任何 PII 实体检测方法：原 `detect_pii_entities` 已于 S4 删除（2026-06-15），人名/机构名检测迁至本地 NER（`privacy/ner.py::PIIGuard.detect_entities`，名字不出本机）。

**调用约定**：
- 输入：单段 markdown 文本（`raw_markdown`）与 `RefineContext`（段序号等上下文）
- 输出：`RefinedResult(markdown, gaps, truncated)`
  - `gaps`：从 LLM 输出中解析出的 `Gap` 列表（LLM 通过注释标记表达缺口位置）
  - `truncated`：是否疑似发生了模型输出截断（详见第 6 节）

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `models.py` | `RefinedResult`, `Gap`, `RefineContext`, `Segment` |
| `pipeline/config.py` | `LLMConfig` |

LLM 层不依赖 OCR/processing/output 的实现细节，只消费文本并产出文本与结构化标记。

## 5. 内部实现

### 5.1 `BaseLLMRefiner`（llm/base.py）

`BaseLLMRefiner` 是云端/本地两种实现共享的公共实现，封装：

- LiteLLM 调用参数拼装（model、重试、超时、base_url/api_key 等）
- 单段精修 `refine()`
- Gap 补充 `fill_gap()`
- 整篇精修 `final_refine()`
- 输出截断标记（`finish_reason == "length"` → `truncated=True`）

接口结构：

```python
class BaseLLMRefiner:
    def __init__(self, config: LLMConfig) -> None: ...

    def _build_kwargs(
        self, messages: list[dict[str, str]]
    ) -> dict[str, object]: ...

    async def refine(
        self, raw_markdown: str, context: RefineContext
    ) -> RefinedResult: ...

    async def fill_gap(
        self,
        gap: Gap,
        current_page_text: str,
        next_page_text: str | None = None,
        next_page_name: str | None = None,
    ) -> str: ...

    async def final_refine(self, markdown: str) -> RefinedResult: ...
```

> 基类不再含 PII 实体检测：原 `detect_pii_entities()` 默认实现已于 S4 删除（2026-06-15），人名/机构名检测迁至本地 NER。

关键点：
- `refine()`：
  1) `build_refine_prompt()` 生成 messages
  2) `litellm.acompletion()` 调用
  3) `parse_gaps()` 从 LLM 输出提取 `Gap` 标记并清理标记本体
- `fill_gap()`：
  - 使用 `build_gap_fill_prompt()` 让 LLM 从 re-OCR 文本中“抽取缺失片段”
  - 若模型返回 `GAP_FILL_EMPTY_MARKER = "无法补充"`，则返回空字符串表示无法填充
- `final_refine()`：
  - 使用 `build_final_refine_prompt()` 对整篇文档去重（跨段重复、页眉水印等）

### 5.2 `CloudLLMRefiner`（llm/cloud.py）

`CloudLLMRefiner(BaseLLMRefiner)` 现为基类的薄子类，仅作 `provider == "cloud"` 的选型标识，纯继承基类的 `refine()/fill_gap()/final_refine()`。

- 原覆盖的 `detect_pii_entities` 及配套私有 helper（`_extract_json_payload` / `_coerce_str_list` / `_CODE_FENCE_RE`）已于 S4 删除（2026-06-15）。
- 人名/机构名检测迁至本地 NER（`privacy/ner.py::PIIGuard.detect_entities`，名字不出本机），不再有云端 LLM 实体识别路径。

### 5.3 `LocalLLMRefiner`（llm/local.py）

`LocalLLMRefiner(BaseLLMRefiner)` 为本地 provider 的实现：

- 纯继承基类的 `refine()/fill_gap()/final_refine()`
- 不再有任何 PII 实体检测方法（原 `detect_pii_entities` 已整链 S4 删除）；PII 脱敏由本地 NER + 正则 + 自定义敏感词承担，数据不出本地

### 5.4 prompt 模板与 GAP 解析（llm/prompts.py）

`prompts.py` 负责所有提示词模板与解析逻辑：

- 分段精修：
  - `REFINE_SYSTEM_PROMPT`
  - `REFINE_USER_TEMPLATE`
  - `build_refine_prompt(raw_markdown, context)`
- 整篇精修：
  - `FINAL_REFINE_SYSTEM_PROMPT`
  - `FINAL_REFINE_USER_TEMPLATE`
  - `build_final_refine_prompt(markdown)`
- Gap 补充：
  - `GAP_FILL_SYSTEM_PROMPT`
  - `GAP_FILL_USER_TEMPLATE`
  - `GAP_FILL_EMPTY_MARKER = "无法补充"`
  - `build_gap_fill_prompt(gap, current_page_text, next_page_text?, next_page_name?)`
- 标题提取：
  - `extract_first_heading(markdown) -> str`（取首个标题作 `PipelineResult.doc_title`）

GAP 标记解析：

- `parse_gaps(refined_markdown) -> (cleaned_markdown, gaps)`
- 解析目标形如：
  - `<!-- GAP: after_image=文件名, context_before="前文", context_after="后文" -->`
- **容错策略**：正则尽力匹配；字段缺失或格式畸形的标记直接忽略，不报错不中断。

> 重要：精修 prompt 只依赖页边界标记 `<!-- page: <原图文件名> -->` 与 GAP 标记，不再依赖任何“段间衔接标记”。

### 5.5 Provider 选择与 PII 接缝

Provider 选择由 Pipeline 完成：

- `LLMConfig.provider == "cloud"` → `CloudLLMRefiner`
- `LLMConfig.provider == "local"` → `LocalLLMRefiner`

PII 接缝策略：

- 两个 refiner 都不再承担 PII 实体检测（原云端 `detect_pii_entities` 已于 S4 删除）。
- 人名/机构名检测统一走本地 NER（`privacy/ner.py::PIIGuard.detect_entities`），与 provider 无关，名字不出本机；脱敏正则与自定义敏感词照常生效。

### 5.6 CodeLLMRefiner（llm/code_refine.py，代码模式字符级精修）

包装 `BaseLLMRefiner._call_llm`，对每个 `SourceFile.merged_text` 跑独立 LLM 调用；**不复用** markdown refine 的截断回退 / GAP 解析。两种模式由 `LLMConfig.code_refine_mode` 选择：

| 模式 | 行为 | 解析键 | 行数约束 |
|---|---|---|---|
| `refine`（默认） | 字符级修正（白名单：OCR 噪声、明显拼写错） | `corrected_code` / `corrections` / `unresolved` | 严格 `输出 == 输入`，违反则回退原文 |
| `rewrite` | 允许重排格式、合并断行、补编译必需语法元素 | `rewritten_code` / `summary` | 不强制行数（结构修复优先） |

入口：`async def refine(source: SourceFile) -> CodeRefineResult`。大文件按 `_should_chunk_refine` 自动分片 refine 后拼接，行号偏移由 `_offset_corrections` / `_offset_unresolved` 修正。失败/超时/解析失败一律回退原文 + 标 `code.refine.*` flag，不抛异常打断同任务其他文件。

返回 `CodeRefineResult(refined_text, flags, corrections=[CodeCorrection], unresolved=[CodeUnresolved])`，由 Pipeline 写回 `SourceFile.merged_text` 与 `flags`。

### 5.7 DiagnosticCodeRepairer（llm/code_repair.py，诊断驱动 scoped repair）

适用于诊断器报告 `syntax_dirty` 的 `SourceFile`。流程：

1. **build_repair_contexts**（线程内跑，含 rglob/read_text 阻塞 IO）按诊断 `failing_lines` ± `window_radius` 切出多个小窗口；窗口互不重叠（`_merge_line_windows` 保证间隔）。
2. 逐窗口调 LLM 拿 scoped patch（`ScopedPatch` 含 `edit_range` + `new_text`）。
3. **行号重映射**：patch/edit_range 都是原文行号，按前面 patch 累计 `line_offset` 平移到 `current` 文本坐标系再应用（B7 C2 关键修复）。
4. **行数守恒兜底**：标记 truncating patch（`_is_truncating_patch`）按 reject 处理，避免 LLM 把窗口尾巴截没。
5. **隔离诊断共置兄弟文件**：repair 在临时副本里跑诊断时，把同目录头文件一起放进去，否则缺失 include 会被判 `dependency_dirty(score 0)` 骗过接受门（B7 C13 自审 followup）。

失败/无窗口/全部 reject 时返回原文 + `code.repair.no_windows` 或 `code.repair.reject_*` flag。

### 5.8 CodeConsistencyAuditor（llm/code_repair.py，repair 后接受门）

repair 后的二次审查。流程：

1. 若 repair **改变了行数**，必须基于改写后文本**重新诊断**（`diagnose_source_files([audit_source])`），不能复用 pre-refine 的原文行号诊断（否则授权窗口对不上行号，B7 C3 关键修复）。
2. `audit()` 接受 `previous_result` 与重诊断结果，按"诊断分数未恶化"判定接受 patch；恶化则拒绝并回退到 repair 前文本。
3. 把 audit 的 `flags` / `unresolved` 合并进最终 `CodeRefineResult`。

## 6. 截断检测（Truncation detection）

精修输出的“被截断”会导致：
- 文档尾部缺失
- 代码块/表格/列表不闭合
- GAP 标记不完整

系统采用两级检测并把结果写入 `RefinedResult.truncated`：

1) **模型级信号**：当 `litellm` 返回 `finish_reason == "length"`，直接判定 `truncated=True`。

2) **启发式信号（Pipeline 层）**：当模型未显式标记截断，但输出行数相对输入行数出现异常下降（行数比例阈值 + 最小输入行数），Pipeline 会把该段结果标记为疑似截断并输出告警日志。

   启发式阈值来自 `LLMConfig`，按任务粒度生效：

   | 字段 | 默认 | 含义 |
   |---|---|---|
   | `truncation_ratio_threshold` | `0.3` | 输出行数少于 `输入行数 × (1 - ratio)` 时视为截断 |
   | `truncation_min_input_lines` | `20` | 输入行数 ≤ 此值时不触发启发式（样本小误判率高） |

   仅当 refiner 自报的 `truncated=False` 时才做启发式（避免双重判定）。

最终，Pipeline 会汇总所有段与整篇精修的截断告警，作为结果 warnings 返回给上游。

## 7. 数据流（与 Pipeline 的对接）

LLM 层在完整处理流程中的典型调用路径如下（省略非 LLM 模块细节）：

```
MergedDocument.markdown
    │
    ├─（可选）PII 脱敏：本地 NER PIIGuard.detect_entities()（名字不出本机；原云端 detect_pii_entities 已 S4 删除）
    │
    ▼
processing.segmenter.DocumentSegmenter.segment()
    │
    ▼
BaseLLMRefiner.refine()  × N 段
    │    └─ parse_gaps() → gaps
    ▼
Pipeline._reassemble()  # 简单 join
    │
    ├─（可选）缺口补充：BaseLLMRefiner.fill_gap()  + OCR.reocr_page()
    │
    └─（可选）整篇精修：BaseLLMRefiner.final_refine()
         └─ parse_gaps()（最终精修也可能产生新的 gap）
```
