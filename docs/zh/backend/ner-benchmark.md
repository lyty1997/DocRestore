<!--
Copyright 2026 @lyty1997
Licensed under the Apache License, Version 2.0 (the "License").
-->

# 本地 NER benchmark 留证（S3.6）

> 日期：2026-06-14 ｜ 上游：[pii-local-ner.md](pii-local-ner.md) §7（强制 benchmark）
> 结论：**spaCy 本地 NER 达标，按计划切换**（人名召回 0.92；机构名略低由结构化正则 + 宽松边界兜底）。
> 复跑：`bash scripts/setup_ner.sh && python scripts/benchmark_ner.py`（docrestore env）。

## 0. 方法

切本地 NER 前必须留「本地 vs 现状云端」的对照证据（§7）。无用户图片的人名/机构金标，
故用「自建小金标 + 真实样本测速 + 云端银标」三件套：

- **金标** `tests/privacy/fixtures/ner_eval.jsonl`：**自建** 26 条中英文短句（**非用户数据集**，
  不违反「禁写死数据集标识符」——这是我们自己的评测语料），覆盖中文名 / 英文名 / 公司 / 机构 /
  干扰项（电话、邮箱、身份证号——这些**不应**被判成人名/机构名）。
- **打分**：归一（去空白 + 小写）后——**严格** = 集合相等；**宽松召回** = 金标实体被任一预测
  「包含或被包含」即算命中（容忍边界差，如 `Dr. Emily Brown` vs `Emily Brown`）。
- **测速**：优先 `test_images/**/result.mmd`（真实 OCR 文本，仅测吞吐不做内容断言）；本机无 OCR
  样本时回退金标语料。
- **云端银标对照已随 S4 移除（2026-06-15）**：原可选 `--cloud` 路径（跑云端 `detect_pii_entities` 对同一金标打分作银标参考）连同云端实体检测链路一并删除，脚本不再支持 `--cloud`。下方判定以**金标绝对召回**为准（云端银标本就非真值，仅作参考）。

脚本 `scripts/benchmark_ner.py`；模型 `zh_core_web_md` + `en_core_web_md`（CNN，禁 `*_trf`）。

## 1. 本地 spaCy 抽取质量（对自建金标，26 条）

| 类别 | 金标 | TP | FP | FN | 精确 | 召回 | F1 | 宽松召回 |
|---|---|---|---|---|---|---|---|---|
| 人名 PER | 24 | 22 | 11 | 2 | 0.67 | **0.92** | 0.77 | **0.92** |
| 机构 ORG | 23 | 17 | 4 | 6 | 0.81 | 0.74 | 0.77 | **0.87** |

## 2. 速度（主进程 CPU）

- 样本来源：金标语料（本机 test_images 无 OCR `result.mmd` 回退）。
- 26 段 / 共 1070 字符：单段均值 **8.8 ms**，p95 10 ms，吞吐 ~4,700 字符/秒。
- 结论：相对 OCR（秒级/页）与云端 LLM 精修（秒级/段）可忽略；且检测走
  `asyncio.to_thread` 卸载，不阻塞事件循环（S3.3）。

## 3. 云端银标对照（已随 S4 移除）

本节为历史记录。S3.6 当次未取得有效数字：`.env` 当时 `LLM_MODEL` 为网关 gemini 模型，其密钥与
`GLM_API_KEY` 不匹配，云端调用返回 `AuthenticationError: Invalid token`，脚本按设计**优雅跳过**
（不阻断本地证据）。

云端 LLM 本身非真值（有自身误差），仅作银标参考；判定一直以**金标绝对召回**为准（见 §4）。因此
**云端银标对照已于 S4 删除（2026-06-15）**——`benchmark_ner.py` 的 `--cloud`/`--cloud-model`/
`--cloud-api-base` 参数连同云端 `detect_pii_entities` 链路一并移除，脚本不再支持云端对照。

## 4. 判定（是否切本地 NER）

**达标，按 [pii-local-ner.md](pii-local-ner.md) 计划切换。** 依据：

1. **人名召回 0.92（严格＝宽松）** —— 隐私最关键的类别（真实姓名）召回高，兑现「名字不出本机」。
2. **机构名召回 0.74 严格 / 0.87 宽松** —— 偏低，6 个 FN 多为简称/边界差异；符合设计 §9.2
   「本地优先、接受略低召回、**结构化正则 + 自定义敏感词兜底**」的取舍。需更高召回可换 `lg` 模型。
3. **精确率偏低（PER 0.67 / 11 FP）属可接受** —— 误检方向是 **over-redact（多脱敏）**，对隐私
   **安全**（§1.3）；且生产路径 `_is_safe_entity` + 高频/超长告警进一步过滤短词/高频误检。
4. **零环境冲突 + 速度可忽略** —— spaCy CNN 不碰 OCR venv 的 torch/transformers（§1.1），
   CPU 单段 ~9ms 不阻塞主链路。

**遗留 / 后续**：① 机构召回若实测不够，换 `*_core_web_lg` 或补领域词典；② 真实 OCR 文本测速
（生成 test_images OCR 后复跑 `--samples-dir`）。（原「补云端银标一致率」一项已随 S4 删除云端
对照路径而取消。）
