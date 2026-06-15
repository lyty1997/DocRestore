<!--
Copyright 2026 @lyty1997
Licensed under the Apache License, Version 2.0 (the "License").
-->

# PII 脱敏统一设计（PIIGuard + 本地 NER）

> 状态：**S1–S4 已落地**（S4 于 2026-06-15 删除云端 detect 死路代码，全门禁绿，§6）。
> 触发：#36 修复后用户提出「文档/代码/PPT 三模式统一 PII 路径，不要分模式管理」。
> 决策已拍板：① 统一到一个 `PIIGuard`；② 结构化 PII **一次前置**（含代码 `text_lines`，即 option 2）；③ 加**本地 NER**，人名/机构名也不出本机。

## 1. 目标与非目标

**目标**
- 把分散在 producer / `_redact_code_pii` / 各云端边界 / `detect_pii_entities` 的脱敏逻辑收进**一个 `PIIGuard`**；模式分支（`_stream_process` / `_code_pipeline` / `_ppt_pipeline`）里**零脱敏代码**。
- 结构化 PII（正则）在 **producer 一次脱完**，覆盖所有模式的所有文本载体（`cleaned_text` + `text_lines`），下游一律消费已脱敏文本。
- 人名/机构名检测改为**本地 NER**，让「上云前全脱完」名副其实——对齐产品北极星「数据不出本机、云中继只做哑加密管道」（桌面服务 + 手机配对方向）。

**非目标**
- 不改 `patterns.py` 的结构化正则规则本身（手机/邮箱/证件/银行卡/凭据/host/内部 URL 不动）。
- 不改流式 Pipeline 的生产者/消费者并行模型（不回退批量版）。
- 不引入新的云端 PII 第三方服务。

## 2. 现状与「散」的根因

### 2.1 当前脱敏分布

| 阶段 | 谁做 | 覆盖 | 模式 |
|---|---|---|---|
| 结构化（正则） | `_ocr_producer` 对 `page.cleaned_text` `redact_regex_only` | 手机/邮箱/证件/卡/凭据/host/内链 + 自定义词 | 文档/PPT（**代码不走 cleaned_text，覆盖不到**） |
| 结构化（正则） | `_redact_code_pii` 对 `merged_text`（header+body） | 同上 | **仅代码**（因载体是 `text_lines→merged_text`） |
| 实体（人名/机构名） | `detect_pii_entities` → **云端 LLM** 检测，`redact_snippet` 应用 | 人名/机构名 | 三模式各自在 cloud 边界应用 |
| 送云端兜底 | `_make_regex_redactor`（#36 新增）/ `redact_snippet` 散在各 refiner | file_path/片段/诊断/分段/页 | 各模式各写 |

### 2.2 散的两个根本原因（不是随意散，是被结构逼的）

1. **文本载体 + 产生时机不同**：文档/PPT 脱 `page.cleaned_text`（OCR 清洗后 markdown，**producer 阶段就有**）；代码脱 `merged_text`（由 `text_lines` 行级 bbox 在 `_code_pipeline` 里 `ide_layout→assemble_columns→group_into_files` **组装之后**才出现）。所以「分模式前一次脱」当前物理上漏掉代码——那时 `merged_text` 还不存在。
2. **粒度不同**：文档/PPT 正文可全文替换人名；代码正文**不能**（会把 import 路径 / namespace / 标识符当人名替换，AGE-50），只有注释头能替人名。

### 2.3 #36 即「散」的直接代价

三个独立的洞，每个都是某条脱敏支路漏了一处：① 代码 header 拼接顺序写反（先送检后脱）；② gap-fill/终结化回落 `self._config.pii`；③ 代码 prompt 的路径/外部片段/诊断从未纳入脱敏面。**散 = 易漏 = 每漏一处就泄一处。** 统一的根本收益是把「N 条支路各自正确」变成「一条主干正确」。

## 3. 目标架构：`PIIGuard`

### 3.1 单一抽象，职责

```
PIIGuard(pii_cfg)                                    # 请求级配置构造，贯穿全任务
  ├─ redact_structured(text, *, profile)  -> text    # 纯本地正则（patterns.py），幂等
  │      profile="full"        全量（手机/邮箱/证件/卡/凭据/host/内链 + 自定义词）
  │      profile="tokens_only" 仅高置信密钥（sk-/gh_/AKIA/JWT）+ 自定义词（零误伤代码）
  ├─ detect_entities(text)                -> Lexicon # 本地 NER（§5），任务内一次或增量
  └─ redact_for_cloud(text, lexicon, *, profile) -> text
                                                     # 送任何云端调用前的统一闸口：
                                                     # redact_structured(profile) + 实体(可选)
```

- **`redact_structured`**：本地正则，幂等（占位符不被二次匹配）。`profile="full"` 给文档/PPT 与代码**头部注释**；`profile="tokens_only"` 给代码**正文**——只拦硬编码密钥（`sk-`/`AKIA`/JWT）+ 自定义词，绝不误伤 `password = get_secret()` 这类正常代码（用户决策 2026-06-14「稳一点」，见 §4.2）。
- **`detect_entities`**：改为**本地 NER**，不再上云（§5）。任务内调一次（流式可增量累积）。
- **`redact_for_cloud`**：所有云端调用点（分段精修 / gap-fill / PPT 每页 / code refine·repair·audit / file_path·片段·诊断）**只认这一个闸口**。文档/PPT 与代码头部传 lexicon 做实体替换；代码正文 `lexicon=None` + `profile="tokens_only"`（不替实体保护标识符）。

### 3.2 调用拓扑（模式分支零脱敏代码）

```
producer:   OCR → clean → guard.redact_structured(page.cleaned_text, "full") → 入队
              ↓（文档/PPT 下游永远见不到结构化 PII）
            代码不在 producer 脱（正文不扫全文，§4），结构化在代码路径按 header/body 分档
detect:     任务内一次 guard.detect_entities(累积文本) → lexicon（本地 NER，不上云）
cloud 边界: 任何送云端的文本一律 guard.redact_for_cloud(text, lexicon, profile)
              · 文档分段 / PPT 每页：lexicon 实体替换 + 结构化("full")幂等
              · 代码头部注释：redact_for_cloud(header, lexicon, "full")
              · 代码正文 body：redact_structured(text, "tokens_only")（不改坏代码）
              · file_path/片段/path_candidates/diagnostics：保持 full（保 #36，§9.5）
                redact_for_cloud(text, None, "full")
```

对比现状：`_redact_code_pii` **保留但瘦身**为「header `full` + body `tokens_only`」并改由 `guard` 实现；`_make_regex_redactor` **保留**（prompt 字段仍走 `full`，内部已改走 `guard`，2026-06-14 决策见 §9.5）；`_finalize_single_doc`/`_fill_one_gap`/各 refiner 里的 bespoke 脱敏调用全部换成 `guard.redact_for_cloud`。

## 4. 结构化 PII：文档/PPT 前置 + 代码头部专扫（option 2 已否决）

### 4.1 文档 / PPT：producer 一次前置（不变）

producer 在 `cleaner.clean(page)` 后、入队前，对 `page.cleaned_text` 调 `guard.redact_structured(text, "full")`。下游分段/精修/输出全部见不到结构化 PII。**现状已如此，仅换成走 `guard`。**

### 4.2 代码：头部全量 + 正文仅高置信 token（用户决策「稳一点」，2026-06-14）

代码**不**在 producer 前置、**不**扫正文全文。结构化在代码路径按段差异化：

| 段 | profile | 含 | 理由 |
|---|---|---|---|
| **头部注释块**（leading comment） | `full` + 实体 + 自定义词 | 手机/邮箱/证件/卡/凭据/host/内链 + 人名/机构名 + 自定义词 | 真 PII（Author/Copyright/联系方式）都在这里 |
| **正文 body** | `tokens_only` | 仅 `sk-`/`gh?_`/`AKIA`/JWT 高置信密钥 + 自定义词 | 拦硬编码密钥（正文真正危险的 PII），但**零误伤代码** |

**为什么正文只留 token**：正文跑全量正则会**改坏代码**——`redact_credential` 的 KV 正则把 `password = get_secret()` 右侧吞成 `password = [凭据]`；16 位常量被当银行卡、`1[3-9]` 开头 11 位数字被当手机号。高置信 token 格式（20+ 字符固定前缀）**碰不到正常代码**，却正好拦住硬编码 `sk-`/`AKIA` 密钥——稳与不漏密钥两头占。自定义词是精确匹配、用户主动配，零误伤，正文也保留。

**prompt 字段（file_path/片段/path_candidates/diagnostics）保持 `full`**（2026-06-14 决策，§9.5）：#36 刚把这些字段纳入脱敏面（vector ③），降 `tokens_only` 会让其中的邮箱/手机重新外发、削弱 #36。这些字段**不是会被执行的代码**，`full` 正则即便轻微改写外部片段，也只影响发给 LLM 的上下文质量、不影响最终产物，故选「PII 保护 > 上下文保真」。`_make_regex_redactor` 因此**保留**（产出 `full` 脱敏函数下传 refiner，内部已走 `guard`）。`tokens_only` 仅作用于会被当代码执行的 body。

### 4.3 为什么否决 option 2（producer 前置扫 `text_lines`）

初版设计想把代码 `text_lines` 也在 producer 前置脱以「彻底统一」。否决，因为：① 与「正文不扫全文」决策直接冲突；② 前置脱敏改文本会扰动**依赖文本内容**的代码启发式——`group_into_files` 跨页重叠比对若单侧被脱会失配（中风险）、`code_path_reconcile` 路径 fuzzy match 也读文本。**正文不扫 = 这些风险全部消失**，设计反而更简单、更稳。代码结构化脱敏因此留在代码路径（`merged_text` 组装后按 header/body 分档），不进 producer。

## 5. 本地 NER（人名/机构名不出本机）

### 5.1 为什么不是造轮子

人名/机构名识别是成熟的 NER 任务，**先选开源**（第一性原理）。候选对比（约束：CPU 优先——GPU 留给 OCR；许可证须宽松，本项目 Apache-2.0；中文 PER/ORG 为主，代码注释含英文名需兼顾）：

| 方案 | 许可证 | 中文 PER/ORG | 体量 / 速度 | 与本项目契合 |
|---|---|---|---|---|
| **Baidu LAC** | Apache-2.0 | 强（分词+词性+NER 一体） | 轻，CPU 快 | **复用 paddle 生态**（OCR 即 PaddleOCR）；中文最契合 |
| **GLiNER** | Apache-2.0 | 中（多语言 zero-shot） | ~200M transformer，CPU 偏重 | 多语言一把抓，**代码英文名**覆盖好 |
| spaCy `zh_core_web_*` | MIT | 中 | 中等 | 成熟稳，依赖独立 |
| HanLP | Apache-2.0 | 很强 | 重（依赖大） | 中文最强但偏重 |

**决策（2026-06-14）：benchmark 后再定**——S3 阶段 LAC 与 GLiNER **都接上**，用 test_images 真实样本测召回/速度/依赖代价（§5.4）再选,不盲选。LAC 中文最契合 + 复用 paddle；GLiNER 零新增依赖（transformers 已在环境）+ 补代码英文名。

> **⚠️ 已被取代（S3 落地，2026-06-14）**：S3 动手前按「不造轮子先调研」核实**推翻**本决策——GLiNER 硬依赖 `transformers≥4.51.3`，撞 vllm/DeepSeek-OCR 锁定的 `4.46.3`（装上破坏 OCR 环境，上文「transformers 已在环境」判断有误）；LAC 2021 停更 + 强耦合老 paddle，且 PaddleOCR 的 paddle 在子进程、主进程「复用」红利不成立。二者均不可用 → 最终**选 spaCy CNN**（`zh/en_core_web_md`，零 torch/transformers，不撞 OCR venv）。详见 [pii-local-ner.md](pii-local-ner.md) §1。

### 5.2 接口与放置

```
LocalEntityDetector(Protocol):
    def detect(text: str) -> tuple[list[str], list[str]]   # (persons, orgs)
```

- `PIIGuard.detect_entities` 内部委托 `LocalEntityDetector`，**取代** `refiner.detect_pii_entities` 的云端调用。
- **放置**：NER 模型常驻**主进程 CPU**（与 OCR 子进程的 paddle 隔离，避免 GPU 争用与版本冲突）；首次用时惰性加载，shutdown 释放。若选 LAC 且与 OCR worker 的 paddle 版本冲突，则把 NER 也放进一个独立 worker（复用现有 worker 基建）。
- `provider="local"` / `"cloud"` 不再决定「名字是否上云」——**一律本地 NER**；`provider` 仅决定精修走云/本地。云端精修时送出的已是脱敏后文本。

### 5.3 准确度与失败策略

- 本地 NER 召回不如大模型属预期 → 保留现有 `block_cloud_on_detect_failure` 语义：检测**异常**（加载失败/崩溃）时 fail-closed 停云端精修（退化为本地输出）；检测**正常但漏检个别名字**属能力边界，结构化 PII 仍由正则兜底。
- 误检（把普通词当人名全局替换）由现有 `_is_safe_entity` + 高频/超长告警兜底（redactor.py 既有防线）。

### 5.4 强制 benchmark（落地前置门槛）

切换前必须用**真实样本**（test_images 的文档 + 代码注释头）对比「本地 NER」vs「现状云端 LLM 检测」的 PER/ORG 召回/精确，留输入→输出证据。**召回明显劣化则不切**（或 LAC+GLiNER 并用补召回）。不允许「装上就当好用」。

## 6. 迁移计划（分步、每步独立验收、行为可对照）

| 步 | 内容 | 验收 |
|---|---|---|
| **S1** | 抽 `PIIGuard`，把现有所有脱敏调用**原样收口**到它（`redact_structured`/`redact_for_cloud` 先包住现逻辑，`detect_entities` 暂仍委托云端）。**行为零变化**。 | 全量测试与现状逐字节一致；mypy/ruff/typos 绿 |
| **S2** | 代码正文 body 降 `tokens_only`（`_redact_code_pii` body 行改档），header 仍 `full`；新增 `tokens_only` 正则原语（仅 `sk-`/`gh?_`/`AKIA`/JWT + 自定义词）。**prompt 字段（file_path/片段/诊断）保持 `full`，`_make_regex_redactor` 保留**（§9.5）。文档/PPT producer 已在 S1b 走 `guard.redact_structured`（`full`）。 | 正文不再被全量正则改坏（`password=expr` 取证）；硬编码 `sk-` 仍被拦；#36 回归（含 prompt 字段 full）仍绿 |
| **S3** ✅已落地 | 本地 NER：`privacy/ner.py::SpacyEntityDetector`（spaCy，**非** LAC/GLiNER，见 [pii-local-ner.md](pii-local-ner.md) §1.1）；`PIIGuard.detect_entities` 切本地 + 一键环境配置（`GET/POST /ner/*` + 前端 TaskForm）；§5.4 benchmark 留证。 | ✅ [ner-benchmark.md](ner-benchmark.md)（人名召回 0.92）；名字不再出现在云端调用入参（mock 取证）；前端三态 Playwright 验证 |
| **S4** ✅已落地 | 清理：已于 S4 删除（2026-06-15）旧云端 `detect_pii_entities` 调用路径（`CloudLLMRefiner`/`BaseLLMRefiner.detect_pii_entities` + `LLMRefiner` Protocol 声明）+ `PIIRedactor.redact_for_cloud(refiner)` + `build_pii_detect_prompt`/`PII_DETECT_SYSTEM_PROMPT`；文档 `privacy.md`/`pipeline.md`/`llm.md` 已同步。 | ✅ 死路代码已删（2026-06-15）+ 全门禁绿（文档已同步） |

每步一个 `feature/pii-unify-sN` 分支，独立 PR，逐个闭环（禁止多个半成品并行）。

## 7. 测试计划

- **S1 等价性**：录制现状各模式脱敏输入→输出快照，S1 后逐字节比对（重构不改行为）。
- **S2 代码分档**：`tokens_only` 只命中 `sk-`/`AKIA`/JWT、**不碰正常代码**（`password = get_secret()` 不被改）的单测；头部仍全量；文档/PPT producer 走 guard 的等价性。
- **S3 本地 NER**：mock 云端记录入参断言**无任何**人名/机构名/结构化 PII 外发（端到端）；benchmark 召回对照表入库。
- **回归**：#36 的 5 个测试 + `test_entity_redaction.py` / `test_pii_early_window.py` 全绿。
- 断言一律从输入派生（不写死数据集标识符，遵循项目测试规则）。

## 8. 工程量评估

**判定：刚刚好（但不小）。** 理由：
- S1（收口）是纯重构、低风险、立刻降「散」——即使后面 S3 暂缓，S1+S2 已消掉 #36 那类「漏一条支路」的结构性隐患，**性价比最高**。
- S3（本地 NER）是唯一「新组件」工程，成本集中在选型 + benchmark + 准确度调优；但它兑现「数据不出本机」的产品承诺，与北极星一致，不是镀金。
- **不是过度工程**：没有引入用不上的抽象（PIIGuard 三方法都有真实调用方）；没有回退批量版。
- **稳**：否决 option 2 后，代码正文不前置脱、不扫全文 → 不碰代码启发式、不改坏代码；唯一残留权衡是正文非 token 类 PII（如正文里的邮箱）不脱，属用户「稳一点」的明确取舍。

**节奏（已定）**：S1→S2 先做（统一收口 + 文档/PPT 前置 + 代码 header/body 分档，立竿见影降散）；S3 本地 NER 作为紧接的独立里程碑（LAC+GLiNER benchmark，本地优先）。

## 9. 决策记录（已确认 2026-06-14）

1. **本地 NER 选型**：S3 阶段 LAC 与 GLiNER **都接上**，用 test_images 真实样本 benchmark（召回/速度/依赖代价）再定，不盲选。
2. **召回取舍**：**本地优先**——接受本地 NER 召回略低于云端 LLM，换「名字不出本机」（结构化正则仍兜底）；**不保留**云端检测路径（仅 PIIGuard 接口留口，benchmark 不过才回退）。
3. **节奏**：**S1+S2 先落**，**S3 本地 NER 单独里程碑**。
4. **代码正文范围**：代码**不扫正文全文**——正文只 `tokens_only`（高置信密钥 `sk-`/`AKIA`/JWT + 自定义词），头部注释全量；**option 2（producer 前置扫 `text_lines`）否决**（§4）。
5. **代码 prompt 字段档位**：`file_path`/`related_snippets`/`path_candidates`/`diagnostics` **保持 `full`**（不随正文降 `tokens_only`）——保住 #36 vector ③ 的 PII 保护（否则片段/诊断/路径里的邮箱/手机重新外发）；`_make_regex_redactor` **保留**（不删）。`tokens_only` 仅作用于会被当代码执行的 body。

## 10. 相关文档

- [privacy.md](privacy.md) - 现状 PII 脱敏层（§9 全链路实体脱敏、§10 #36 修复）
- [pipeline.md](pipeline.md) - Pipeline 数据流
- 产品北极星「数据不出本机」（桌面服务 + 手机配对 + 云中继哑加密管道）
