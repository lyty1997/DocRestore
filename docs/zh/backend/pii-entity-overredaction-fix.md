<!--
Copyright 2026 @lyty1997
Licensed under the Apache License, Version 2.0 (the "License").
-->

# PII 实体脱敏误伤修复（结构损坏 + 误检）详细设计

> 状态：**已落地**（2026-06-18，分支 `bugfix/pii-entity-overredaction`）——按默认决策
> （D1 不加停用表 / D2 净化落 redactor / D3 A+B 同出）实现。真实样本验证：图片路径 /
> `FGRFP`·`RXNFP` / LaTeX / HTML 结构全完好，替换数 43→25（残留为 N1 正文误检）。
> 测试 `tests/privacy/test_redactor.py`（+11）+ `test_ner.py`，门禁 EXIT=0 / pytest 1403 passed。
> 上游：[pii-unification.md](pii-unification.md)（PIIGuard 收口）、[pii-local-ner.md](pii-local-ner.md)（S3 本地 NER）、[pii-cloud-egress-gate.md](pii-cloud-egress-gate.md)（#67 出云闸口）。
> 落地文件：新增 `privacy/markup.py`（结构跨度单一真相源）；改 `privacy/redactor.py`（`_replace_entities` 结构感知+词边界、`_looks_like_name` 净化）、`privacy/ner.py`（检测前 `mask_structure`）。

## 0. 背景与现象

现象：**关 PII 输出质量正常；开 PII 后大量误伤英文专有名词与图片标识符**。对照样本：

- 无 PII：`/tmp/docrestore_83b24032/document.md`
- 开 PII：`/tmp/docrestore_1a52b705/document.md`

典型损坏（开 PII 后）：

| 原文 | 损坏后 | 说明 |
|---|---|---|
| `…编码方法FGRFP` | `…编码方法[机构名]FP` | 词内子串 `FGR` 被替 |
| `RXNFP` | `[机构名]FP` | 词内子串 `RXN` 被替 |
| `images/微信图片_…_501_94_after_1.jpg` | `images/微信图片_…_[机构名]_1.jpg` | **图片 src 路径被改** |
| `…break-word;'>kcat  $ \mu $L/min` | `…break-word[人名]  $ [人名] $L/min` | **HTML 属性分隔符 `;'>` 与 LaTeX `\mu` 被吞** |
| `Ec-YihU</td>` | `Ec-Yih[人名]/td>` | HTML 标签碎片 `U<` 被替，破坏 `</td>` |
| `Metallosphaera sedula` | `[人名] sedula` | 物种名误检为人名 |

## 1. 根因——两层缺陷相乘

### 1.1 检测层：把"结构内容"喂给通用 NER（`privacy/ner.py`，S3 引入）

`pipeline.py` 的 `_detect_entities` / `_delayed_pii_detect`（如 `:2400`、`:2413`）把
`merger.get_markdown()`——**含图片引用、HTML 表格、LaTeX、代码的完整 markdown**——
原样喂给 spaCy 通用 CNN（`zh_core_web_md` + `en_core_web_md`）。复现实测，词表含：

```
PERSON: ";'>kcat"  '\mu'  'U<'  'Metallosphaera'  'AKHSDH2' …（含真名 'Yang Li' 'Dehang'）
ORG:    '501_94_after_1.jpg'  '501_94_after'  'FGR'  'RXN'  '以2,4'  'L)-aspartate-4'
        'Nucleic Acids Research'  '不同于以前的方法，FGRFP能够…'（整句被判 ORG）  …
```

下游唯一过滤 `_is_safe_entity`（仅"长度≥2 且含字母数字"）挡不住任何上述项。

### 1.2 替换层：无边界、无结构保护的 `str.replace`（`privacy/redactor.py`）

`_replace_entities`（`:93`）核心是：

```python
text = text.replace(name, placeholder)   # 全局子串替换，无词边界、无结构豁免
```

只要词表里有一条坏词，就全文穿透替换：穿透单词内部（`FGR`→`FGRFP`）、穿透图片 src
路径、穿透 HTML 属性分隔符、穿透 LaTeX。`apply_lexicon` 的 docstring（`:174-179`）
宣称"对结构化文本一律安全"——**与 `str.replace` 的实际行为矛盾**，是错误的安全假设。

两层相乘：检测吐结构碎片 × 替换无差别全文 = 系统性损坏。这解释了"关 PII 正常、开 PII 炸"。

> 边界澄清：手机/邮箱/证件/卡等**结构化 regex 脱敏不是肇事者**；问题专属
> `redact_person_name` / `redact_org_name` 的实体（人名/机构名）替换路径。

## 2. 目标 / 非目标

**目标**
- G1 **结构零损坏**（必达）：实体替换绝不修改图片/链接目标、HTML 标签属性、行内/围栏代码、LaTeX `$…$`、URL/路径。
- G2 **无词内误命中**：ASCII 实体不得匹配更长单词的子串（`FGR` ↛ `FGRFP`）。
- G3 **降低误检入表**：明显非"名字"的候选（文件名、标签碎片、整句、数字串）不进词表。
- G4 行为可回归：以从输入派生的断言覆盖（禁写死数据集标识符，遵 `CLAUDE.md` 测试规则）。

**非目标**
- N1 不追求消灭通用 NER 对"长得像名字的领域词"（如 `Metallosphaera`、期刊名）的误检——这是通用 NER 的固有精度上限，本设计只把它压到"偶发遮一个正文词"的可接受量级。
- N2 不更换 NER 模型 / 不引入领域微调（成本与收益不匹配，维持 [pii-local-ner.md](pii-local-ner.md) 选型）。
- N3 不改结构化 regex 脱敏与出云闸口（#67）的既有契约。

## 3. 方案

两层都改、互补防御。A 是高杠杆主刀（即便检测不完美也兜底结构），B 减少垃圾入表。

### A. 替换层：结构感知 + 词边界（`redactor.py::_replace_entities`）

复用本文件已有的"**保护区切分**"范式（`_replace_custom_words` + `_placeholder_split_re`
把占位符当保护区，奇数段原样、偶数段才替换），扩展到**结构保护区**：

1. 构造一条"结构跨度"正则，按下列模式把文本切成 **保护段 / 自由段**（顺序即优先级）：
   - 围栏代码 ```` ```…``` ````、行内代码 `` `…` ``
   - HTML 标签 `<[^>]+>`（覆盖 `<img …>`、`<td …>`、`</td>`——保护标签内 src/属性）
   - markdown 图片/链接 `!?\[[^\]]*\]\([^)]*\)`
   - LaTeX 数学 `\$.*?\$`（OCR 产 `$ 1/s $` 这类，非贪婪）
   - URL `https?://\S+`
2. **只在自由段**做实体替换；保护段原样拼回。
3. 自由段内替换按类型分流：
   - 纯 ASCII 词形实体 → 词边界正则 `(?<![A-Za-z0-9])NAME(?![A-Za-z0-9])`（解 G2）。
   - 含 CJK / 连字符等非 `\w` 的实体 → 维持精确串替换（CJK 无词边界，且本就少嵌入）。
4. 计数与 `_HIGH_FREQ_WARN` / `_LONG_ENTITY_WARN` 告警逻辑保留。

> 效果：`<img src="…501_94_after_1.jpg">`、`…break-word;'>`、`$ \mu $`、`</td>` 全在保护段，
> 无论词表多脏都不被改；`FGRFP` 因词边界不再被 `FGR` 命中。表格**单元格内的正文**
> （如 `Saccharomyces cerevisiae`）仍是自由段——真名仍可被遮，但 `;'>` 等标签结构不再破。

### B. 检测层：正文化 + 词表净化（`ner.py` / 检测调用点）

- **B1 喂正文**：检测前先把 §A 的同一组"结构跨度"mask 成空白再交给 spaCy，使
  `501_94_after_1.jpg` / `;'>kcat` / `\mu` / `</td>` 不进 NER 视野。实现上把"结构跨度正则"
  抽成 `redactor` 与 `ner` 共用的单一定义（单一真相源，避免两处漂移）。
- **B2 词表净化**：`_collect_entities` 产出后过一道"**像不像名字**"校验，丢弃：
  - 含结构/markup 字符（`/ \ < > $ { } ; ( ) [ ] | =` 与反引号）的候选；
  - 以文件扩展名收尾（`\.(jpe?g|png|gif|svg|pdf|md|txt)$`，大小写不敏感）；
  - 字母/CJK 占比 < 0.5（数字串、`以2,4`、`L)-aspartate-4` 这类）；
  - 长度 > `_LONG_ENTITY_WARN`（整句）——由"告警仍执行"改为"**丢弃**"。
  - 净化是**召回换精度**的取舍：带数字/符号的真实机构名（极少见）会被放过，换取零结构误伤。
  - **#95 收窄（2026-06-30）**：半角撇号 `'` 与双引号 `"` 原在结构字符集，导致 NER 正向判定的
    含撇号西文人名（`O'Brien` / `d'Angelo`）被整条丢弃、确定性放走出云。撇号/双引号是人名内
    **合法标点**（非结构字符），已从 `_MARKUP_CHARS` 剔除；这类人名落自由文本，由 §A
    `split_protected` 结构保护 + `_sub_in_free` ASCII 词边界安全替换。`;'>kcat` 这类碎片仍因
    `;`/`>` 被丢弃，overredaction 防护不退化。

> B 把结构垃圾挡在词表外；A 把结构挡在替换外。任一单独都不够：只做 B，残留一条坏词仍会
> 经 `str.replace` 破结构；只做 A，正文里的 `Metallosphaera` 仍会被遮（属 N1 容许范围）。

## 4. 接口 / 数据契约

- `EntityLexicon` 结构、`PIIConfig`（`redact_person_name` / `org_name` / `*_placeholder` /
  `ner_models`）、`PIIGuard` 公开方法签名**全不变**。
- 新增内部纯函数（不出包）：结构跨度正则构造器（`redactor` 与 `ner` 共用）、词表净化 `_looks_like_name`。
- `apply_lexicon` docstring 的"对结构化文本一律安全"**改为属实**（结构保护落地后该承诺才成立）。

## 5. 测试计划（断言全部从输入派生，禁硬编码数据集标识符）

`tests/privacy/test_redactor.py` / `test_ner.py` 增量，构造**合成** markdown：

- **T1 结构零损坏**：输入含 `<img src="images/foo_bar_1.jpg">` + 词表含 `foo_bar_1.jpg` →
  断言 src 路径原样、占位符未出现在 `<img …>` 内。
- **T2 词边界**：词表含 ASCII 短词 `ABC`，正文含 `ABCDEF` 与独立 `ABC` → 断言只替独立 `ABC`，`ABCDEF` 不变。
- **T3 LaTeX/HTML 豁免**：输入含 `$ \alpha $` 与 `<td style='…;'>x</td>`，词表含 `\alpha`、`;'>x` →
  断言数学段与标签段不变。
- **T4 自由正文仍替**：保护段之外的句子里放一个构造的人名 → 断言被替（保证没误杀召回）。
- **T5 词表净化**：`_looks_like_name` 对构造的 `a.jpg` / `x<y` / `12,3` / 超长句返回 False，对 `Zhang Wei` 返回 True。
- **T6 幂等**：对已脱敏文本再跑一次，占位符不被二次破坏（沿用 #61 不变量）。

断言均从"测试自己构造的输入串"派生关键短语，符合 `CLAUDE.md` 测试规则。

## 6. 改动面

| 文件 | 改动（已落地） |
|---|---|
| `backend/docrestore/privacy/markup.py` | **新增**：结构跨度正则单一真相源 `STRUCTURE_SPAN_RE` + `split_protected` + `mask_structure` |
| `backend/docrestore/privacy/redactor.py` | `_replace_entities` 改结构感知（`split_protected`）+ 词边界（`_sub_in_free`）；新增 B2 净化 `_looks_like_name`（D2 单点收口）；`_LONG_ENTITY_WARN`→`_MAX_ENTITY_LEN` 丢弃；修正 `apply_lexicon` docstring |
| `backend/docrestore/privacy/ner.py` | `_collect_entities` 检测前 `mask_structure`（B1） |
| `backend/docrestore/pipeline/pipeline.py` | 无改动（检测/替换仍走 guard，签名不变） |
| `tests/privacy/test_redactor.py` | 更新高频用例为词边界语义；新增 `TestLooksLikeName`(5) + `TestEntityReplacementStructureSafe`(6) |
| `docs/zh/known-issues.md` | 补"已知问题→解决"条目 |

## 7. 风险 / 取舍

- **R1 结构正则误判**：贪婪/非贪婪不当可能漏保护或过保护。缓解：非贪婪 + 单测 T1/T3 覆盖；复杂嵌套（代码里含 `$`）以"代码段优先切分"消解。
- **R2 B2 召回损失**：带数字的真实机构名被放过（见 §3 取舍）。属可接受——本就以结构化 regex + 自定义词兜底高危 PII，人名/机构名是"尽力遮"语义。
- **R3 CJK 词边界**：CJK 实体维持精确串替换，可能仍有 CJK 子串命中（如"北大"命中"东北大学"）——现状即如此，本次不扩大处理，按长度降序已部分缓解。

## 8. 待确认决策

- **D1 B2 激进度**：是否加英文常用词停用表（进一步压 `Swapping`/`Supervised` 这类）？默认**不加**（停用表难维护、易漂移），先靠"喂正文 + 结构净化"。
- **D2 净化位置**：B2 放 `ner._collect_entities`（贴近产出）还是 `redactor`（贴近消费）？倾向 `redactor`，使任何来源的 lexicon（含 code 路径）都过净化，单点收口。
- **D3 落地方式**：单分支一刀（A+B 一起）还是先 A 止血再 B？倾向**一起**（A 无 B 仍遮正文词、观感未根治）。

## 9. 工程量判断

**刚刚好（偏精简）**：损坏严重到使"开 PII"对任何技术文档不可用，必须修；A+B 复用既有保护区范式、零新依赖、不动公开契约、改动集中在 2 个文件 + 测试。不做 N1/N2/D1 那类高成本低边际收益的项，避免过度工程；只做 A（欠工程）则正文误检观感仍在，故 A+B 同出。

