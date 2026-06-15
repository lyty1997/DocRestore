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

    def redact_snippet(
        self, text: str, lexicon: EntityLexicon | None,
    ) -> tuple[str, list[RedactionRecord]]:
        """对短片段（如 re-OCR 文本）做脱敏：结构化正则 + 自定义词，
        再复用已有 `EntityLexicon` 替换人名/机构名（词表为 None 则退化为仅正则）。"""

    def redact_regex_only(
        self, text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """仅结构化正则 + 凭据 + 自定义词脱敏，不做实体替换（供流式 producer 逐页先行）。"""

    def redact_tokens_only(
        self, text: str,
    ) -> tuple[str, list[RedactionRecord]]:
        """仅高置信密钥（sk-/gh?_/AKIA/JWT）+ 自定义词脱敏，供代码正文 body 用，
        不跑 KV/手机/邮箱全量正则以免吞坏代码。"""
```

> 送云端的统一闸口是 `PIIGuard.redact_for_cloud(text, lexicon, *, profile)`（sync，第二参数
> 是 lexicon），实体词典由本地 NER `PIIGuard.detect_entities(text)` 产出，见 §10.5。
> 原 `PIIRedactor.redact_for_cloud(text, refiner)`（async、依赖云端 refiner 检测实体）已于
> S4 删除（2026-06-15）。

### 3.2 EntityLexicon

```python
@dataclass(frozen=True)
class EntityLexicon:
    """本地 NER 检测到的实体词典（不可变，便于跨页复用）。"""
    person_names: tuple[str, ...]
    org_names: tuple[str, ...]
```

> 实体检测失败或未开人名/机构名开关时，`PIIGuard.detect_entities` 返回 `None` 作为 lexicon
> （调用方需判空，`redact_snippet(text, None)` 退化为仅正则）。

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

### 4.2 实体检测（本地 NER）

> **S3（2026-06-14）起本地 NER**：`PIIGuard.detect_entities`（spaCy）检测人名/机构名，名字不出
> 本机；其取代的原云端 `detect_pii_entities` 方法链已于 S4 删除（2026-06-15）。详见 §10.5。

现行本地 NER 路径：
- 调用 `PIIGuard.detect_entities(text)`（`privacy/ner.py::SpacyEntityDetector`）检测人名/机构名
- 返回 `EntityLexicon`（`person_names` / `org_names`），检测失败或未开开关时返回 `None`
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
- 本地 NER 实体检测失败 + `block_cloud_on_detect_failure=True`：跳过所有云端 LLM 调用
- 本地 NER 实体检测失败 + `block_cloud_on_detect_failure=False`：仅使用正则脱敏结果

## 7. 数据流

```
MergedDocument（合并后）
    │
    ▼ 结构化脱敏 + 实体替换（早期批量版整篇路径，流式版见 §9）
    ├─ 正则脱敏（手机/邮箱/身份证/银行卡）
    ├─ 本地 NER 实体检测（可选，人名/机构名，PIIGuard.detect_entities）
    └─ 实体替换（PIIGuard.redact_for_cloud(text, lexicon)）
    │
    ▼ (脱敏后文本, RedactionRecord[], EntityLexicon)
    │
    → 进入 LLM 精修阶段
```

## 8. 注意事项

- 文件名：`patterns.py` 不是 `regex.py`（避免 mypy 模块名冲突）
- 银行卡校验：使用 Luhn 算法降低误报
- 实体检测：走本地 NER（`PIIGuard.detect_entities`，spaCy），与 LLM provider 无关，名字不出本机
- re-OCR 脱敏：缺口补充时的 re-OCR 文本也需要脱敏

## 9. 全链路实体脱敏前置（流式版 · 已落地 2026-06-04）

> §7 的 `redact_for_cloud(MergedDocument)` 是早期**批量版**整篇脱敏路径。当前**流式版** Pipeline 的 PII 脱敏分散在 producer 与精修链路，见本节。来源：max-effort code-review #1 复核（2026-06-04），用户拍板「全链路精修前脱敏（流式 + 输出兜底）」。

### 9.1 流式版现状

- **结构化 PII（正则）**：`_ocr_producer` 对每页 `page.cleaned_text` 调 `redact_regex_only`（手机 / 邮箱 / 身份证 / 银行卡 + 自定义词），**入队前**完成 → 下游全部（分段 / 精修 / 输出 / 全模式含 PPT）都见不到结构化 PII。✅
- **实体 PII（人名 / 机构名）**：文档模式 `_stream_process` 在积累到 `_PII_DETECT_THRESHOLD` 页后调一次 `_delayed_pii_detect` → 本地 NER `PIIGuard.detect_entities` 构建 `EntityLexicon`（S3 前为云端 `detect_pii_entities`，已于 S4 删除）。

### 9.2 缺口

`EntityLexicon` 目前**只用于 gap-fill 重 OCR 片段**（`_fill_one_gap` 内 `redact_snippet`）。文档主分段精修、PPT 按页精修、最终输出**都未应用它** → 开了 `redact_person_name` / `redact_org_name`，人名 / 机构名仍原样进入云端精修调用、并留在最终输出。属**全链路既有缺口**（文档 + PPT 都中招），非 PPT 独有、非某次提交引入。

### 9.3 设计：流式 + 输出兜底

应用点（复用现成 `redact_snippet(text, lexicon)`，gap-fill 既有路径不动）：

1. **文档主分段**：`_stream_process` 把 `entity_lexicon` 透传到分段精修点，在 `_refine_segment_with_cache` 调用前 `redact_snippet(seg_text, lexicon)`。
2. **PPT 每页**：`_ppt_pipeline` 签名加 `pii_cfg`，按已收页文本构建 lexicon，每页精修前 `redact_snippet(body, lexicon)`。
3. **最终输出兜底**：`_finalize_single_doc` 组装末尾 `redact_snippet(final_md, lexicon)`，覆盖 lexicon 就绪前已精修的"早窗口"段；PPT 组装结果同样兜底。

关键约束 / 决策：

- 保持文档流式（不收齐全文），早窗口靠输出兜底覆盖。
- 实体检测走本地 NER（`PIIGuard.detect_entities`，S4 已取代原「沿用所配置 refiner」的云端检测，2026-06-15），名字不出本机；详见 §10.5。
- `lexicon=None`（未开 name 开关 / 检测失败）→ `redact_snippet(text, None)` 退化为仅正则，零改动、不阻断精修。

### 9.4 验收

- 文档主分段、PPT 每页送精修前：词表内人名 / 机构名已替换。
- 早窗口已精修段含人名 → 最终 `document.md` 兜底后不含。
- `pii.enable=False` 或两 name 开关都关 → 精修入参 / 输出与基线完全一致。
- 检测失败（lexicon=None）→ 仅正则，不抛异常、不阻断。

落地（已实现，2026-06-04）：

- [x] 核心：`_refine_segment_with_cache` 加 `redactor` + `entity_lexicon` kwargs，
  在缓存查找前 `redact_snippet`（缓存键用脱敏后文本，resume 一致）；文档主分段
  与 PPT 按页共用此入口。
- [x] 助手：抽 `_detect_entities(text, llm, pii_cfg)`（`_delayed_pii_detect` 委托它），
  PPT 与短文档兜底复用。
- [x] 文档：`_stream_process` 建 `redactor` + 透传 `entity_lexicon` 到
  `_try_extract_and_refine` 与尾段；页数不足阈值时结尾补建一次词表。
- [x] PPT：`_ppt_pipeline` 接 `pii_cfg`，积累页文本到阈值建词表、每页精修前应用，
  组装前对 `bodies` 做输出兜底（覆盖早窗口页）。
- [x] 输出兜底：`_finalize_single_doc` render 前对 `doc.markdown` 再 `redact_snippet`。
- [x] 回归：`tests/pipeline/test_entity_redaction.py`（核心脱敏 / 无词表不改 /
  检测开关 / PPT 输出兜底 / 关脱敏零改动且不调检测）；`check_quality.sh` 全绿
  （pytest 1025 passed）。

## 10. 上云前脱敏多路绕过修复（#36 · 2026-06-14）

> 来源：2026-06-13 安全审查（High）。§9 落地后仍有三条路径绕过「上云前脱敏」。标准部署（启动级 `PIIConfig.enable=False`、前端按单次任务开 PII）下用户以为开了 PII，实则多路失效。

### 10.1 三条绕过路径与修复

| # | 绕过点 | 现象 | 修复 |
|---|---|---|---|
| ① | `_redact_code_pii`（代码模式 header 实体检测） | `combined` 用**原始** header 拼接后 `detect_pii_entities(combined)` 云端调用，结构化 PII 的 regex 脱敏在该调用**之后**才执行 → 注释里 `Author: 张三 <a@corp.com>` 的邮箱/手机随 combined 裸送云端 | 拼 `combined` **前**先对每个 header `redact_regex_only`（结构化 PII / 凭据 / 自定义词先掉）；人名不被 regex 触及，实体检测仍正常 |
| ② | `_finalize_single_doc`(:2177) / `_fill_one_gap`(:3047) | 读 `self._config.pii`（启动默认 `enable=False`）而非**请求级** `pii_cfg` → 用户单次开 PII 走请求级，这两个 gate 恒 False → gap-fill re-OCR 文本（绕过 producer 逐页 regex 的全新文本）与最终输出实体兜底**不脱敏** | `pii_cfg` 一路透传 `_stream_process → _finalize_single_doc → _maybe_fill_gaps → _fill_gaps → _fill_one_gap`，全程用请求级配置，禁止回落 `self._config.pii` |
| ③ | 代码 prompt 的 `file_path` / `related_snippets`（含外部 `context_root` 片段）/ `path_candidates` / `diagnostics` | refine/rewrite/repair/audit prompt 把上述字段拼进云端调用，未脱敏；其中 repair 诊断在脱敏前算（g++ `summary=output[:1000]` 带 caret 时回显含 PII 的源码行） | 请求级 `pii_cfg` 建 `redact_regex_only` 函数下传三类 refiner；在 `json.dumps` **之前**对这些字段按字段脱敏 |

### 10.2 关键设计

- **请求级配置单一真相源**：`pii_cfg = pii or self._config.pii`（`_stream_pipeline`）一次解析后，三模式分支与所有下游 helper 全用 `pii_cfg`；启动级 `self._config.pii` 仅作为「请求未带」时的回退默认，不在任何 helper 里二次读取。
- **代码 prompt 先脱后序列化**：vector ③ 的脱敏发生在构造 dataclass / 拼 f-string **之前**，`json.dumps` 随后对占位符里任何引号（用户可自定义 placeholder / custom word code）正确转义 → 绝不破坏 JSON。snippets / path / diagnostics 只 `redact_regex_only`（结构化 PII + 凭据 + 自定义词，**不做实体替换**，避免误伤 import 路径 / namespace / 标识符，与 `_redact_code_pii` 正文处理同口径）。
- **PPT 模式经核查为干净**：`_ppt_pipeline` 自 §9 起即正确透传 `pii_cfg`、producer 逐页 regex、每页精修前 `redact_snippet`、组装兜底 + fail-closed，无 `self._config.pii` 误读，本次不改。

### 10.3 验收（已实现，2026-06-14）

- [x] ②：请求级开 PII（`self._config.pii.enable=False`）时 gap-fill re-OCR 文本与最终输出确被脱敏 —— `test_request_level_pii_redacts_reocr_text` / `test_finalize_output_uses_request_pii_when_startup_off`（含「回退 bug 必失败」反验证）。
- [x] ①：header 含 `<a@corp.com>` 时送 `detect_pii_entities` 的入参邮箱 / 手机已掩码、人名仍保留 —— `test_header_structured_pii_masked_before_detect`。
- [x] ③：`file_path` / 外部参考片段 / `diagnostics` 里结构化 PII 在 prompt 中已脱敏且产物仍是合法 JSON —— `test_redact_masks_prompt_fields` / `test_file_path_redacted_in_refine_prompt` + 对照 `test_no_redact_leaves_prompt_fields_raw`。
- [x] 门禁：`mypy --strict` / `ruff` / `typos` 全绿；PII + 代码模式相关测试 140 passed。

### 10.4 后续统一（PIIGuard 收口 + 代码正文 tokens_only，2026-06-14）

#36 修复后把三模式脱敏统一到 `PIIGuard`（`redact_structured` / `redact_for_cloud`，详见
[PII 统一设计](pii-unification.md)）：

- **S1**：所有脱敏调用点收口到 `PIIGuard`，行为逐字节不变。
- **S2 代码档位差异化**：代码**正文 body** 降 `tokens_only`（仅高置信密钥 `sk-`/`gh?_`/`AKIA`/JWT
  + 自定义词），不再跑 KV/手机/邮箱全量正则——否则 `password = get_secret()` 右侧被吞坏代码；
  代码**头部注释**仍 `full`（真 PII 都在注释里）。**prompt 字段（file_path/片段/path_candidates/
  diagnostics）保持 `full`**，不削弱 #36 vector ③ 的保护；文档/PPT 正文不变（仍 `full`）。

### 10.5 本地 NER：人名/机构名不出本机（S3，2026-06-14 已落地）

实体检测从**云端 LLM**（`refiner.detect_pii_entities`）迁到**本地 NER**（spaCy `zh/en_core_web_md`，
CNN，零 torch/transformers，不撞 OCR venv），兑现「名字不出本机」。接缝从 LLM 层下移到隐私层：

- `PIIGuard.detect_entities(text) -> EntityLexicon | None`（`privacy/ner.py::SpacyEntityDetector`，
  进程级惰性单例，PERSON→persons / ORG→orgs）**取代** `CloudLLMRefiner.detect_pii_entities`；
  Pipeline 5 处检测调用点全部改接，`asyncio.to_thread` 卸载阻塞；新增
  `PIIConfig.ner_backend ("spacy"|"none")` + `ner_models`。
- **fail-fast**：开人名/机构名脱敏但 spaCy/模型未装 → 建任务 400 `NER_BACKEND_UNAVAILABLE`
  （`remediable=true`）；`GET /ner/status` 探测 + `POST /ner/setup` 一键装环境（前端 TaskForm 三态 UX）。
- **fail-closed**：运行期检测异常 → `lexicon=None`，`block_cloud_on_detect_failure` 阻断云端精修。
- **已于 S4 删除（2026-06-15）**：原 `PIIRedactor.redact_for_cloud(refiner)`（async）、
  云端 `detect_pii_entities`（`CloudLLMRefiner` / `BaseLLMRefiner` / `LLMRefiner` Protocol）、
  `build_pii_detect_prompt` + `PII_DETECT_SYSTEM_PROMPT`、§9.3 原「检测沿用所配置 refiner」约束
  —— 均已被本地 NER 取代，对应代码（`llm/cloud.py`/`base.py`/`prompts.py`/`privacy/redactor.py`）已清除。
- 选型（spaCy 而非 LAC/GLiNER，含环境冲突原委）与 benchmark 留证：见
  [pii-local-ner.md](pii-local-ner.md) §1 / [ner-benchmark.md](ner-benchmark.md)（人名召回 0.92 达标）。

## 11. 相关文档

- [PII 统一设计](pii-unification.md) - PIIGuard 收口 + 代码正文 tokens_only + 本地 NER 规划
- [PII 本地 NER 详细设计](pii-local-ner.md) - S3 spaCy 选型 + 接缝 + 一键环境配置
- [NER benchmark 留证](ner-benchmark.md) - S3.6 spaCy 召回/速度实测
- [数据模型](data-models.md) - `RedactionRecord`, `PIIConfig`
- [LLM 层](llm.md) - 云端 `detect_pii_entities` 已于 S4 删除（2026-06-15），本地 NER 取代
- [Pipeline](pipeline.md) - PII 脱敏在数据流中的位置
