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

# PII 脱敏层

## 1. 概述

PII（Personally Identifiable Information）脱敏层在文档发送到云端 LLM 前，对敏感信息进行脱敏处理，降低隐私风险。

位置：`backend/docrestore/privacy/`

## 2. 模块结构

```
privacy/
├── patterns.py    # 结构化 PII 正则（手机/邮箱/身份证/银行卡）
└── redactor.py    # PIIRedactor + EntityLexicon
```

## 3. 核心接口

### 3.1 PIIRedactor

```python
class PIIRedactor:
    def __init__(self, config: PIIConfig) -> None: ...

    async def redact_for_cloud(
        self,
        text: str,
        refiner: LLMRefiner | None,
    ) -> tuple[str, list[RedactionRecord], EntityLexicon | None]:
        """对合并文档进行 PII 脱敏（供云端 LLM 使用）。

        流程：
        1. 结构化正则脱敏（手机/邮箱/身份证/银行卡）
        2. 自定义敏感词按 word 长度全局降序替换（每词用自己的 code，空则回落）
        3. 若 refiner 非空，调用 refiner.detect_pii_entities() 检测人名/机构名
        4. 构建 EntityLexicon 并替换实体

        Returns:
            (脱敏后文本, 脱敏记录列表, 实体词典 | None)
        """

    def redact_snippet(
        self, text: str, lexicon: EntityLexicon | None,
    ) -> tuple[str, list[RedactionRecord]]:
        """对短片段（如 re-OCR 文本）复用已有实体词典做脱敏，不再调用 LLM。"""
```

### 3.2 EntityLexicon

```python
@dataclass(frozen=True)
class EntityLexicon:
    """LLM 检测到的实体词典（不可变，便于跨页复用）。"""
    person_names: tuple[str, ...]
    org_names: tuple[str, ...]
```

> 实体检测失败或本地 provider 下，`redact_for_cloud` 返回 `None` 作为 lexicon（调用方需判空）。

## 4. 脱敏策略

### 4.1 结构化 PII（正则）

- 手机号：`1[3-9]\d{9}`
- 邮箱：标准邮箱正则
- 身份证：18 位（含校验位）
- 银行卡：13-19 位 + Luhn 校验

默认替换占位符（均可在 `PIIConfig` 中覆盖）：
- 手机：`[手机号]`（`phone_placeholder`）
- 邮箱：`[邮箱]`（`email_placeholder`）
- 身份证：`[身份证号]`（`id_card_placeholder`）
- 银行卡：`[银行卡号]`（`bank_card_placeholder`）

### 4.2 实体检测（LLM）

仅在云端模式下可选启用：
- 调用 `CloudLLMRefiner.detect_pii_entities()` 检测人名/组织名
- 返回 JSON：`{"person_names": [...], "org_names": [...]}`
- 构建 EntityLexicon 并替换实体

默认替换占位符：
- 人名：`[人名]`（`person_name_placeholder`）
- 机构名：`[机构名]`（`org_name_placeholder`）

## 5. 配置

`CustomWord` / `PIIConfig` 都是 pydantic `BaseModel`（全部配置统一迁移到 pydantic）。

```python
class CustomWord(BaseModel):
    """自定义敏感词条目。code 非空时用它做替换，否则回落到 custom_words_placeholder。"""
    model_config = ConfigDict(frozen=True)  # 可 hash
    word: str
    code: str = ""

class PIIConfig(BaseModel):
    enable: bool = False                          # 是否启用 PII 脱敏
    block_cloud_on_detect_failure: bool = True    # 实体检测失败时是否阻断云端调用
    custom_sensitive_words: list[CustomWord] = []
    custom_words_placeholder: str = "[敏感词]"    # 未指定代号时的默认占位符
    # 其它字段详见 data-models.md §4.8
```

API 层 `CustomSensitiveWord`（`api/schemas.py`）是 pydantic 请求模型，接受 `list[str] | list[{word, code?}]`；路由 `_to_custom_words()` 将其统一转为 `CustomWord` 进入 `pii_override`。

### 自定义敏感词 → 代号映射

为了缓解同一占位符大量重复造成的阅读困难，允许用户为每个敏感词指定独立代号：

- `CustomWord(word="张伟", code="化名A")` → 文本中 `张伟` 被替换为 `化名A`。
- `CustomWord(word="某公司")`（未填 code）→ 回落为默认占位符 `[敏感词]`。
- 替换顺序仍按 `word` 长度降序，防止短词先匹配（如「张伟」先于「张伟强」）。
- `RedactionRecord` 按实际使用的 placeholder 聚合计数，多代号场景产生多条记录。

## 6. 失败策略

- 正则脱敏失败：记录 warning，继续流程
- 实体检测失败 + `block_cloud_on_detect_failure=True`：跳过所有云端 LLM 调用
- 实体检测失败 + `block_cloud_on_detect_failure=False`：仅使用正则脱敏结果

## 7. 数据流

```
MergedDocument（合并后）
    │
    ▼ PIIRedactor.redact_for_cloud()
    ├─ 正则脱敏（手机/邮箱/身份证/银行卡）
    ├─ LLM 实体检测（可选，人名/组织名）
    └─ 实体替换
    │
    ▼ (脱敏后文本, RedactionRecord[], EntityLexicon)
    │
    → 进入 LLM 精修阶段
```

## 8. 注意事项

- 文件名：`patterns.py` 不是 `regex.py`（避免 mypy 模块名冲突）
- 银行卡校验：使用 Luhn 算法降低误报
- 实体检测：仅在云端模式下可用（LocalLLMRefiner 无此能力）
- re-OCR 脱敏：缺口补充时的 re-OCR 文本也需要脱敏

## 9. 全链路实体脱敏前置（流式版设计 · 待实现）

> §7 的 `redact_for_cloud(MergedDocument)` 是早期**批量版**整篇脱敏路径。当前**流式版** Pipeline 的 PII 脱敏分散在 producer 与精修链路，见本节。来源：max-effort code-review #1 复核（2026-06-04），用户拍板「全链路精修前脱敏（流式 + 输出兜底）」。

### 9.1 流式版现状

- **结构化 PII（正则）**：`_ocr_producer` 对每页 `page.cleaned_text` 调 `redact_regex_only`（手机 / 邮箱 / 身份证 / 银行卡 + 自定义词），**入队前**完成 → 下游全部（分段 / 精修 / 输出 / 全模式含 PPT）都见不到结构化 PII。✅
- **实体 PII（人名 / 机构名）**：文档模式 `_stream_process` 在积累到 `_PII_DETECT_THRESHOLD` 页后调一次 `_delayed_pii_detect` → `detect_pii_entities` 构建 `EntityLexicon`。

### 9.2 缺口

`EntityLexicon` 目前**只用于 gap-fill 重 OCR 片段**（`_fill_one_gap` 内 `redact_snippet`）。文档主分段精修、PPT 按页精修、最终输出**都未应用它** → 开了 `redact_person_name` / `redact_org_name`，人名 / 机构名仍原样进入云端精修调用、并留在最终输出。属**全链路既有缺口**（文档 + PPT 都中招），非 PPT 独有、非某次提交引入。

### 9.3 设计：流式 + 输出兜底

应用点（复用现成 `redact_snippet(text, lexicon)`，gap-fill 既有路径不动）：

1. **文档主分段**：`_stream_process` 把 `entity_lexicon` 透传到分段精修点，在 `_refine_segment_with_cache` 调用前 `redact_snippet(seg_text, lexicon)`。
2. **PPT 每页**：`_ppt_pipeline` 签名加 `pii_cfg`，按已收页文本构建 lexicon，每页精修前 `redact_snippet(body, lexicon)`。
3. **最终输出兜底**：`_finalize_single_doc` 组装末尾 `redact_snippet(final_md, lexicon)`，覆盖 lexicon 就绪前已精修的"早窗口"段；PPT 组装结果同样兜底。

关键约束 / 决策：

- 检测沿用所配置 refiner（不强制本地）；保持文档流式（不收齐全文），早窗口靠输出兜底覆盖。
- **`detect_pii_entities(text)` 本身要把文本送给 refiner**——若 refiner 是云端，则检测调用本身上云一次。"人名完全不出本机"须配 `provider="local"`（本设计不强制，仅记边界）。
- `lexicon=None`（未开 name 开关 / 检测失败）→ `redact_snippet(text, None)` 退化为仅正则，零改动、不阻断精修。

### 9.4 验收

- 文档主分段、PPT 每页送精修前：词表内人名 / 机构名已替换。
- 早窗口已精修段含人名 → 最终 `document.md` 兜底后不含。
- `pii.enable=False` 或两 name 开关都关 → 精修入参 / 输出与基线完全一致。
- 检测失败（lexicon=None）→ 仅正则，不抛异常、不阻断。

落地任务（待实现）：

- [ ] `_stream_process` 主分段精修前应用 lexicon（透传 `entity_lexicon`）
- [ ] `_ppt_pipeline` 接 `pii_cfg` + 建 lexicon + 每页精修前应用
- [ ] `_finalize_single_doc` 输出兜底（文档 + PPT）
- [ ] 边界回归：关开关零改动 / 检测失败退化仅正则 / 结构化正则不受影响
- [ ] 单测随各项落地；`scripts/check_quality.sh` 全绿

## 10. 相关文档

- [数据模型](data-models.md) - `RedactionRecord`, `PIIConfig`
- [LLM 层](llm.md) - `CloudLLMRefiner.detect_pii_entities()`
- [Pipeline](pipeline.md) - PII 脱敏在数据流中的位置
