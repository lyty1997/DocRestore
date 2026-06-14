<!--
Copyright 2026 @lyty1997
Licensed under the Apache License, Version 2.0 (the "License").
-->

# PII 本地 NER 详细设计（S3）

> 状态：**设计待确认**（2026-06-14）
> 上游：[pii-unification.md](pii-unification.md) §5（本地 NER）、§6 迁移计划 S3、§9 决策。
> 脊柱决策：**spaCy 主进程**（2026-06-14 用户确认，详见 §1.1 为何偏离 §9 原定 LAC+GLiNER）。

## 0. 本文定位

S3 是 [pii-unification.md](pii-unification.md) §6 迁移计划的第三步，也是唯一引入「新组件」的一步。上游 §5/§9 已拍**架构层**：人名/机构名检测改本地 NER、本地优先（接受略低召回、结构化正则兜底）、强制 benchmark、fail-closed。本文细化**模块层**：选定 spaCy、定义代码接缝、依赖门控、失败/降级策略、benchmark 工程、测试与迁移步骤。

S1（PIIGuard 收口）、S2（代码正文 `tokens_only`）已落地进 dev（PR #58）。S3 之后还有 S4（清理云端 `detect_pii_entities` 死路 + 文档收尾），不在本文范围。

## 1. 选型结论（spaCy，2026-06-14 确认）

### 1.1 为何偏离 §9 原定「LAC+GLiNER 都接」

§9 拍板时假设两者都能塞进主进程做 benchmark。S3 动手前按「不造轮子先调研」核实两库**当前真实状态**，发现两条与该假设冲突的硬约束：

| 候选 | 维护状态 | 关键依赖 | 与本项目 venv 冲突 |
|---|---|---|---|
| **GLiNER** 0.2.26（2026-03，活跃） | ✅ 活跃 | 硬依赖 `torch≥2.0` + **`transformers≥4.51.3`** + onnxruntime | ⚠️ **transformers 撞车**：vllm/DeepSeek-OCR 锁死 `4.46.3`（setup 强制降级），GLiNER 要 ≥4.51.3，同 venv 装不下 |
| **LAC** 2.1.0（**2021-01，停更**） | ❌ 158 issues / 0 PR，5 年未动 | paddlepaddle（老版） | ⚠️ 强耦合老 paddle，现代 Python 装机脆；且 PaddleOCR 的 paddle 在**子进程**，主进程「复用 paddle」红利并不存在 |
| **spaCy** `zh/en_core_web_md`（活跃，MIT） | ✅ 活跃 | **CNN 模型零 torch / 零 transformers**（仅 thinc/numpy） | ✅ **零冲突**——不碰 OCR venv 的 torch/transformers/paddle |

结论：GLiNER 进不了 OCR 那个 venv（撞 transformers），LAC 既停更又须隔离。§5.2 原设的「主进程 CPU 常驻」对这两个都不直接成立，唯独 spaCy 成立。spaCy 的 CNN（非 `trf`）模型自带 NER 组件（OntoNotes 训练，含 `PERSON`/`ORG`），中等召回——正好契合 §9.2「本地优先、接受略低召回、结构化正则兜底」。

> transformers 撞车只在**同时装了 vllm/DeepSeek-OCR** 时咬人（默认 setup 会装）。即便某部署只用 PaddleOCR，选 spaCy 仍是最省心——零新增重依赖。

### 1.2 模型选择

- **默认 `["zh_core_web_md", "en_core_web_md"]`**：中文文档 + 代码注释里的英文作者名两头覆盖。`md`（~75MB/个）是召回与体量的平衡点；要更高召回可换 `lg`（~570MB），要更省可换 `sm`。**均为 CNN 模型，绝不用 `*_trf`**（trf 依赖 transformers，会重新引入 §1.1 的撞车）。
- **标签映射**：spaCy OntoNotes `PERSON` → `person_names`；`ORG` → `org_names`。与现状云端检测的 `(person_names, org_names)` 二元组**完全对齐**，下游 `EntityLexicon` 消费零改动。
- **混合语言**：对每段文本依次过所有已加载模型，**并集** `PERSON`/`ORG` span。中文文档过 en 模型（或反之）只会多出误检 → 由现有 `_is_safe_entity` + 高频/超长告警兜底（误检方向是 over-redact，隐私安全）。

### 1.3 GLiNER 已弃用（破坏环境，不留开关位）

GLiNER **不接、不预留开关位**（2026-06-14 用户决策）——它硬依赖 `transformers≥4.51.3`，与 OCR venv 锁死的 `4.46.3` 直接冲突，**装上即破坏 OCR 环境**。`ner_backend` 因此只有 `"spacy"`/`"none"` 两值（§4），不含 `"gliner"`。若日后 spaCy 召回实测不够，再单独评估**与环境彻底隔离**的方案（独立 venv 的子进程等），届时重新设计、不在本文预埋。`LocalEntityDetector` 协议（§2.2）仍是干净接缝便于将来换实现，但当前**唯一实现就是 spaCy**。

## 2. 接缝设计（§5.2 落地）

### 2.1 `PIIGuard.detect_entities`

`PIIGuard` 当前**无** `detect_entities`（实体检测仍在 Pipeline 侧 `_detect_entities` 委托 refiner 云端）。S3 给 `PIIGuard` 补该方法，**把实体检测从 LLM 层挪进隐私层**：

```python
def detect_entities(self, text: str) -> EntityLexicon | None:
    """本地 NER 检测人名/机构名，返回 EntityLexicon；检测失败返回 None。

    - 未启用人名/机构名脱敏（redact_person_name/org 全 False）→ 返回 None（不检测）。
    - ner_backend="none"（用户显式关本地 NER）→ 返回 None（结构化仍跑，不阻断云端，§5）。
    - 检测成功（含「没找到任何实体」）→ EntityLexicon（可能空）。
    - 检测异常（库不可用/模型崩溃）→ None；调用方按 block_cloud_on_detect_failure fail-closed。
    """
```

语义与现状云端 `_detect_entities` **逐点对齐**：成功（含空结果）→ 非 None lexicon；异常 → None。区别仅在「谁来检测」从云端 refiner 换成本地 detector。

### 2.2 `LocalEntityDetector` 协议 + `SpacyEntityDetector`

新建 `backend/docrestore/privacy/ner.py`：

```python
class LocalEntityDetector(Protocol):
    """本地实体检测器接缝。当前唯一实现 SpacyEntityDetector（GLiNER 已弃用，§1.3）。"""
    def detect(self, text: str) -> tuple[list[str], list[str]]: ...   # (persons, orgs)
    @property
    def available(self) -> bool: ...   # 模型是否就绪（≥1 个配置模型加载成功）


class SpacyEntityDetector:
    """spaCy CNN 模型实体检测。惰性加载，进程级单例复用（§2.3）。"""
    def __init__(self, model_names: Sequence[str]) -> None: ...
    def detect(self, text: str) -> tuple[list[str], list[str]]:
        # 对每个已加载模型跑 nlp(text)，并集 PERSON→persons / ORG→orgs，去重保序
        ...
```

`PIIGuard.detect_entities` 内部委托 `get_detector(self._cfg)`（§2.3）→ `detector.detect(text)` → 组装 `EntityLexicon`。`detector.available is False`（库/模型全缺）时 `detect_entities` 抛 `NERUnavailableError`，由调用方语义化为「检测失败」(返回 None) 走 fail-closed。

### 2.3 进程级惰性单例（模型只加载一次）

spaCy 模型加载昂贵（秒级 + 数十 MB 常驻），**绝不能每任务/每调用重载**。`PIIGuard` 是请求级廉价对象，但其委托的 detector 是**进程级**：

```python
# ner.py 模块级
_DETECTOR_CACHE: dict[tuple[str, ...], SpacyEntityDetector] = {}
_DETECTOR_LOCK = threading.Lock()

def get_detector(cfg: PIIConfig) -> LocalEntityDetector:
    """按配置模型集惰性构造并缓存 detector（进程内一次加载，跨任务复用）。"""
```

- 键 = 排序后的 `tuple(model_names)`；同模型集复用同一实例。
- `threading.Lock` 守护首次加载（避免并发任务重复加载同模型）。
- 关停时无显式释放（spaCy 模型随进程退出回收；无子进程/句柄，不进 shutdown 链）。

### 2.4 类型边界（mypy --strict 下的可选依赖）

spaCy 是**可选依赖**（§6），不能在模块顶层 `import spacy`（未装则 import 失败）。且 spaCy 类型不完整。做法：

- **惰性导入**：`import spacy` 放进 `SpacyEntityDetector._load()`，包 `try/except ImportError` → `available=False`。
- **不写 `Any`**：用最小结构化 `Protocol` 包住实际用到的 spaCy 对象，仅声明 `.ents`（可迭代）、`span.text`、`span.label_`：

```python
class _SpacySpan(Protocol):
    @property
    def text(self) -> str: ...
    @property
    def label_(self) -> str: ...

class _SpacyDoc(Protocol):
    @property
    def ents(self) -> Iterable[_SpacySpan]: ...

class _SpacyNLP(Protocol):
    def __call__(self, text: str, /) -> _SpacyDoc: ...
```

加载得到的 `nlp` 标注为 `_SpacyNLP`，满足 `--strict` 不触 `Any`（符合 typescript/python 规范「禁 Any」）。单测可注入实现该 Protocol 的 fake nlp，**无需下载真实模型**（§8）。

## 3. 调用点改接（Pipeline 改动面）

实体检测当前散在 5 处，全部调 `refiner.detect_pii_entities`（云端）。S3 统一改走 `guard.detect_entities`（本地），**接口契约不变**（返回 `(persons, orgs)` → `EntityLexicon | None`），故 5 处改动是「换检测后端」而非「改调用逻辑」：

| # | 位置 | 现状 | 改为 |
|---|---|---|---|
| 1 | 文档主路 `_delayed_pii_detect`（pipeline.py:~2285）→ `_detect_entities`（:~2258） | `refiner.detect_pii_entities(text)` | `guard.detect_entities(text)` |
| 2 | 短文档兜底（:~1908） | 同走 `_detect_entities` | 同上（随 `_detect_entities` 改造一并生效） |
| 3 | PPT 逐页早窗（:~1195） | 同走 `_detect_entities` | 同上 |
| 4 | PPT 短文兜底（:~1226） | 同走 `_detect_entities` | 同上 |
| 5 | 代码头部 `_redact_code_pii`（:~1668） | 直调 `refiner.detect_pii_entities(combined)` | `guard.detect_entities(combined)` |

**核心改造点 `_detect_entities`**：现签名 `(text, llm, pii_cfg)`，靠 `llm`（refiner）做云端检测。改为**去掉 `llm` 依赖**——内部 `guard = PIIGuard(pii_cfg)`（廉价）→ `guard.detect_entities(text)`（委托进程级 detector）。4 个调用点（#1–#4）随之去掉传 `llm`。#5 单独把直调换成 `guard.detect_entities`。

**保留不动**：
- `block_cloud_on_detect_failure` 的调用点逻辑（检测返回 None → 按开关阻断云端精修）原样保留——只是「None」的来源从「云端抛异常」变成「本地 detector 抛异常/不可用」。
- 云端 `CloudLLMRefiner.detect_pii_entities` + `BaseLLMRefiner.detect_pii_entities` + `build_pii_detect_prompt` **暂留**（S3 只是绕过不调用），由 S4 清理。§9.2 已决「不保留云端检测路径」，但物理删除与 prompt 清理归 S4，避免 S3 改动面过大。

## 4. 配置项（`PIIConfig` 增量）

`backend/docrestore/pipeline/config.py::PIIConfig` 加 **2 个字段**，不新增「实体脱敏总开关」（复用现有 `redact_person_name`/`redact_org_name`，零行为回归）：

```python
#: 本地 NER 后端。"spacy"=spaCy CNN 模型（唯一实现）；"none"=显式关闭本地实体
#: 检测（结构化正则仍跑，不阻断云端，见 pii-local-ner.md §5）。GLiNER 已弃用
#: （破坏环境，§1.3），不设取值。
ner_backend: Literal["spacy", "none"] = "spacy"

#: 本地 NER 模型集（spaCy 模型名）。默认中英双模覆盖中文文档 + 代码英文名。
#: ≥1 个加载成功即「可用」（缺的告警跳过，属召回边界）；全缺则 fail-closed（§5）。
#: 必须用 CNN 模型（*_md/_sm/_lg），禁用 *_trf（依赖 transformers，撞 OCR venv）。
ner_models: list[str] = Field(default_factory=lambda: ["zh_core_web_md", "en_core_web_md"])
```

**门控关系**（不变 + 新增）：
- `enable=False`（默认）→ 全链路不脱敏，NER 不触发。
- `enable=True` 且 `redact_person_name`/`redact_org_name` 全 False → 不做实体检测（现状行为，保留）。
- `enable=True` 且任一 `redact_person/org=True`：按 `ner_backend` 走 §5 策略。

> `ner_models` 用 `Field(default_factory=...)` 而非裸 `list`（pydantic 可变默认陷阱）。

## 5. 失败与降级策略（隐私正确性核心）

「名字不出本机」的承诺要求：**人名/机构名脱敏一旦被请求，就绝不能让未脱敏文本上云**。据此定策略矩阵（行 = 场景，列 = 行为）：

| 场景 | 实体检测 | 结构化正则 | 实体脱敏 | 云端精修 | 信号 |
|---|---|---|---|---|---|
| `ner_backend="none"`（用户显式关） | 不跑 | 照跑 | 不做 | **不阻断**（用户知情放弃实体脱敏） | INFO 日志一次 |
| `="spacy"` 模型就绪，检出 N 实体 | lexicon | 照跑 | 替换 | 正常（已脱敏文本上云） | — |
| `="spacy"` 但 **spaCy 未装 / 配置模型全缺** | 不可用 | 照跑 | 不做 | **fail-closed**（按 `block_cloud_on_detect_failure`，默认阻断） | 错误码 `NER_BACKEND_UNAVAILABLE` |
| `="spacy"`，运行期模型抛异常 | 失败(None) | 照跑 | 不做 | **fail-closed**（同现状 §5.3） | WARN 日志 |
| 多模型**部分**缺失（装了 zh 缺 en） | 部分 lexicon | 照跑 | 用可用模型替换 | 正常 | WARN 一次（缺失模型，提示安装） |
| 误检（普通词被当人名） | — | — | over-redact（多脱） | 正常 | 现有 `_is_safe_entity` + 高频/超长告警兜底 |

**两条关键边界**：

1. **「不可用」与「没检出」严格区分**：spaCy 全缺 / import 失败 = **不可用** → fail-closed（用户想脱却脱不了，绝不能放行上云）；模型就绪但文本里没人名 = **检出空** → 正常放行（空 lexicon，结构化已脱）。`SpacyEntityDetector.available` 区分二者。
2. **可用性「fail-fast 优先」**：在**任务创建时**（请求级校验）就探测——若 `enable && (redact_person||org) && ner_backend=="spacy"` 但 spaCy/模型不可用，直接 **400 拒绝建任务**（`NER_BACKEND_UNAVAILABLE`，附 actionable message：装 `ner` extras 与模型 / 或设 `ner_backend=none` / 或关 `redact_person_name`+`redact_org_name`），避免白跑 OCR。探测用 `importlib.util.find_spec("spacy")` + `spacy.util.is_package(model)`（**不加载模型**，廉价）。运行期崩溃 fail-closed 作为兜底第二防线。该 400 响应带 `remediable=true`，前端据此弹**一键配置本地 NER 环境**入口（§6.2/§6.3/§6.5）——用户点确认即自动装环境，而非只甩一句报错。

**「≥1 模型即可用」的取舍**：部分缺失（如只装 zh）按召回边界处理（warn + best-effort），不 fail-closed——与 §5.3「漏检个别名字属能力边界，结构化兜底」一致。要严格「双模缺一即拒」可后续加 `ner_require_all_models` 开关，S3 不做（避免一个模型缺失就整任务挂的脆性）。

## 6. 依赖、安装与一键配置

### 6.1 可选依赖与手动安装

- **可选依赖组**：`pyproject.toml` 加
  ```toml
  [project.optional-dependencies]
  ner = ["spacy>=3.8,<4"]   # 仅 spaCy 核心（thinc/numpy 等小依赖），零 torch/transformers
  ```
  spaCy 版本与项目 `numpy>=1.26` 对齐（spaCy≥3.8 支持 numpy 2.x）。
- **模型单独装**（spaCy 模型是带 URL 的独立 wheel，不能写进 `dependencies`）：
  ```bash
  python -m spacy download zh_core_web_md
  python -m spacy download en_core_web_md
  ```
- **一键脚本** `scripts/setup_ner.sh`：装 `pip install -e '.[ner]'` + 上面两个模型；幂等（已装跳过）。后端「一键配置」（§6.3）与本脚本同源。
- **不污染 OCR venv**：spaCy CNN 路径零 torch/transformers/paddle，与 §1.1 的撞车彻底隔离——可与 OCR 同 venv 共存。

### 6.2 可用性探测 API（`GET /api/v1/ner/status`）

```jsonc
// 响应
{
  "available": false,                         // ≥1 个配置模型就绪？
  "spacy_installed": false,                    // spaCy 包是否已装
  "configured_models": ["zh_core_web_md", "en_core_web_md"],
  "installed_models": [],                      // 上述中已就绪的
  "missing_models": ["zh_core_web_md", "en_core_web_md"]
}
```

廉价探测：`importlib.util.find_spec("spacy")` + 对每个配置模型 `spacy.util.is_package`（**不加载模型**）。前端在用户开启 PII 且勾选人名/机构名脱敏时拉取，用于决定是否弹一键配置入口。

### 6.3 一键配置 API（`POST /api/v1/ner/setup`）

用户开了实体脱敏但环境未就绪 → 前端弹「一键配置本地 NER 环境」→ 点确认调此接口**异步装** spaCy + 缺失模型；返回受理，进度轮询 `GET /api/v1/ner/setup/status`（`state: idle|running|done|failed` + 日志行 + error）。

- **安装机制**：asyncio 子进程依次跑 `[sys.executable, "-m", "pip", "install", "spacy>=3.8,<4"]`，再对每个**缺失且校验通过**的模型跑 `[sys.executable, "-m", "spacy", "download", <model>]`。用 `sys.executable` 保证装进**当前 venv**——detector 惰性 import（安装前未导入 spacy），装完同进程 `import spacy` + `spacy.load` 即生效，**无需重启**。
- **单例 + 锁**：同时只允许一个 setup 在跑（第二次请求 `409`）。
- **子进程生命周期**（遵守 concurrency-resource-safety 规范）：`start_new_session=True` + stdout/stderr `PIPE` 必须有 drain 协程持续 `readline` 消费（写进度缓冲，否则 64KB pipe 写满卡死）+ 保存 task/proc 引用 + shutdown 时 `cancel`+`await` + atexit `killpg` 兜底。

### 6.4 安全（对齐仓库安全审查基线）

- **模型名是 install 子进程唯一的外部可变输入**（`ner_models` 可被请求级覆盖）→ 进 `spacy download` 前**严格校验**：正则白名单 `^[a-z]{2,3}_core_(web|news)_(sm|md|lg)$`，不符即拒、**绝不进子进程**。
- 全程 **list-form subprocess**（无 `shell=True`）、无字符串拼接；命令**固定**为 `pip install spacy` + 校验过的 spaCy 模型，**不接受任意包名**（不是通用 pip 代理）。
- `/ner/setup` 是特权操作（装包）→ 必须走与其它接口一致的**鉴权**（#35 fail-closed token）。
- 安装目标固定当前 venv，不接受用户指定路径/源。

### 6.5 前端 UX（TaskForm）

- 用户在 TaskForm 开启 PII 且勾选人名/机构名脱敏 → 拉 `GET /ner/status`。
- **未就绪**：内联告警 + 「**一键配置本地 NER 环境**」按钮（在配好前**禁止直接提交**）。点击 → `POST /ner/setup` → 轮询进度条（显示安装日志尾行）→ 成功后重新探测、清告警、放行提交；失败显示错误 + 重试/手动安装指引（§6.1）。
- 任务创建 `400 NER_BACKEND_UNAVAILABLE`（`remediable=true`）作为**服务端兜底**（直连 API 绕过前端时），前端据此弹同一配置入口。
- **视觉验证**：按 work-norm 前端规范，改完跑 `scripts/screenshot.js` 截图核对告警态/进度态/就绪态三屏。

## 7. 强制 benchmark（§5.4 落地门槛）

切换前必须留「本地 NER vs 现状云端 LLM 检测」的对照证据，**召回明显劣化则不切**（另议与环境隔离的方案，非 GLiNER，§1.3）。无 test_images 的人名/机构名金标，故采用「自建小金标 + 真实样本测速 + 云端做银标一致率」三件套：

- **脚本** `scripts/benchmark_ner.py`：
  - 输入①小金标 `tests/privacy/fixtures/ner_eval.jsonl`——**自建**的中英文短句，每条标注 `{"text": ..., "persons": [...], "orgs": [...]}`，覆盖中文名/英文名/公司/机构/带占位符干扰项。**自建数据非用户数据集**，不违反「禁写死数据集标识符」（这是我们自己的评测语料，不是对用户图片内容做断言）。
  - 输入②真实样本：test_images 跑 OCR 后的 `result.mmd` 文本（仅测**速度/吞吐**，不做内容断言——遵循项目测试规则从输入派生）。
  - 对每条：跑 spaCy detector → 算 PER/ORG 的 precision/recall/F1（对金标）；可选跑云端 LLM（有 `GLM_API_KEY` 时）算「本地∩云端 / 云端」一致率（银标参考）。
  - 测速：真实样本上 spaCy 单段平均/尾延迟。
- **证据落地** `docs/zh/backend/ner-benchmark.md`：召回/精确/F1 对照表 + 速度 + 结论（spaCy 是否达标、不达标如何处置）。**人手跑、非 CI**（需装模型）。

验收门槛：金标 PER/ORG recall 不显著低于云端（具体阈值跑完按实测定），且单段延迟可接受（主进程 CPU，不阻塞 OCR）。

## 8. 测试计划

CI **不下载真实模型**（体量大），全部用注入式 fake 覆盖逻辑；真实模型质量交给 §7 benchmark（opt-in）。

- **`tests/privacy/test_ner.py`**（新）：
  - 注入实现 `_SpacyNLP` Protocol 的 fake nlp（返回预置 `PERSON`/`ORG` span）→ 验 `SpacyEntityDetector.detect` 的标签映射（PERSON→persons、ORG→orgs）、并集去重保序、多模型合并。
  - 降级：`spacy` 不可导入 → `available=False`；`get_detector` 缓存命中（同模型集返回同实例）。
- **`tests/privacy/test_guard.py`**（扩）：`PIIGuard.detect_entities`——未开人名/机构名 → None；`ner_backend="none"` → None；detector 抛异常 → None（fail-closed 由调用方处理）；正常 → `EntityLexicon`。注入 fake detector，不碰真实 spaCy。
- **`tests/pipeline/test_entity_redaction.py`**（改造）：现状 mock `refiner.detect_pii_entities`，改为 mock `guard.detect_entities` / 注入 fake detector。断言「名字不出云」——mock 云端 refiner 记录入参，断言入参**无任何**人名/机构名（端到端隐私取证）。
- **`tests/api/...`**（新增 1 例）：`enable && redact_person_name && ner_backend="spacy"` 但 spaCy 不可用 → 建任务 400 `NER_BACKEND_UNAVAILABLE`（mock `find_spec` 返回 None）。
- **回归**：#36 的 5 个测试 + `test_pii_early_window.py` + `test_code_pii_header.py` 全绿（实体检测后端换了，契约没换，应全过）。
- 断言一律从输入派生（不写死数据集标识符）。

## 9. 迁移步骤（逐步独立验收，禁止多半成品并行）

| 步 | 内容 | 验收 |
|---|---|---|
| **S3.1** | `privacy/ner.py`：`LocalEntityDetector` 协议 + `SpacyEntityDetector` + `get_detector` 单例 + 类型 Protocol；`test_ner.py`（fake nlp） | 单测绿；mypy/ruff/typos 绿；不下真实模型 |
| **S3.2** | `PIIGuard.detect_entities` + `PIIConfig.ner_backend/ner_models` + `test_guard.py` 扩 | detect_entities 语义单测绿（None/空/异常分支） |
| **S3.3** | Pipeline 5 处改接（`_detect_entities` 去 llm、#5 直调换 guard）+ `test_entity_redaction.py` 改造 + 任务创建 fail-fast 校验（400） | 「名字不出云」mock 取证；#36 + early-window 回归全绿 |
| **S3.4** | NER 环境 API：`GET /ner/status`（探测）+ `POST /ner/setup`（装包子进程，§6.3）+ `GET /ner/setup/status`（进度）+ 模型名白名单校验（§6.4）+ 子进程生命周期挂 shutdown 链 + API 测试 | status/setup 单测绿（mock 子进程）；校验拒非法模型名取证；子进程 drain/cancel 不泄漏 |
| **S3.5** | 前端 TaskForm：开人名/机构名脱敏时拉 `/ner/status`，未就绪内联告警 + 一键配置按钮 + 进度轮询 + 三态 UX（§6.5）；i18n；400 兜底入口 | vitest 绿；`screenshot.js` 截图核对告警/进度/就绪三屏 |
| **S3.6** | `scripts/setup_ner.sh` + `benchmark_ner.py` + `ner_eval.jsonl`，跑真实证据 | `ner-benchmark.md` 有召回/速度对照表 + 达标结论 |
| **S3.7** | 文档：本文转「已落地」、`privacy.md`/`pipeline.md` 同步、`pii-unification.md` §5/§6 状态更新；云端 `detect_pii_entities` 标记死路待 S4 | 文档同步；全门禁绿 |

每步一个 commit（`feat(core)`/`feat(api)`/`feat(tui)`/`test(core)`/`docs`），整体一个 `feature/pii-unify-s3` 分支 → PR base dev → rebase-merge。`Fixes` 关联随同批安全 release 进 main。

## 10. 工程量判定：刚刚好

- **不过度**：spaCy 零冲突最简路径；`LocalEntityDetector` 协议当前单实现，但它是 §5.2 确认的接缝、便于将来换实现——非投机抽象。**不预建任何 NER worker**（GLiNER 已弃用，§1.3）。一键配置功能是用户**明确要求**（报错 → 提示 → 自动装），非镀金；且命令固定、白名单校验，不是通用包管理器。
- **不欠**：fail-fast 校验 + fail-closed 兜底是隐私正确性的硬要求；一键配置是可用性硬要求（否则用户没装环境就被一句报错挡死）；强制 benchmark 是 §5.4 门槛——三者都不能省。
- **风险点**：spaCy 中文 NER 召回中等——但这是 §9.2 明确接受的取舍（本地优先 + 结构化兜底），且 §7 benchmark 兜底验证；若实测不达标，再单独评估**与环境隔离**的方案（非 GLiNER，§1.3）。

## 11. 决策记录（2026-06-14）

1. **选型 spaCy 主进程**（偏离 §9 原定 LAC+GLiNER）：因 GLiNER `transformers≥4.51.3` 撞 OCR venv 的 `4.46.3`、LAC 2021 停更 + paddle 耦合；spaCy CNN 零 torch/transformers，唯一能干净进主进程的。
2. **默认中英双模** `zh_core_web_md` + `en_core_web_md`（CNN，禁 `trf`）；≥1 加载即可用，部分缺失 warn + best-effort。
3. **接缝**：实体检测从 LLM 层（`refiner.detect_pii_entities`）挪进隐私层（`PIIGuard.detect_entities` → `LocalEntityDetector`）；云端检测路径 S3 绕过、S4 清理。
4. **失败策略**：请求级 fail-fast（不可用即 400 `NER_BACKEND_UNAVAILABLE`）+ 运行期 fail-closed 兜底；`ner_backend="none"` 为知情放弃（不阻断）。
5. **GLiNER 弃用**：装上即破坏 OCR 环境（`transformers≥4.51.3` 撞 `4.46.3`），**不接、不留 `ner_backend` 取值**；若 spaCy 不达标再评估与环境隔离的方案（§1.3），不预埋 GLiNER。
6. **环境一键配置**（用户要求）：实体脱敏环境未就绪 → **报错 + 提示 + 提供一键配置功能**（`POST /ner/setup` 装包子进程，§6.3），用户点确认即自动装 spaCy + 模型（装进当前 venv、惰性 import 免重启），而非只甩报错。模型名严格白名单校验、命令固定、走鉴权（§6.4）。

## 12. 相关文档

- [pii-unification.md](pii-unification.md) — PII 统一总设计（§5 本地 NER、§6 迁移计划、§9 决策）
- [privacy.md](privacy.md) — 现状 PII 脱敏层
- [pipeline.md](pipeline.md) — Pipeline 数据流
- 产品北极星「数据不出本机」（桌面服务 + 手机配对 + 云中继哑加密管道）
