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

# 已知问题

## LLM 短段截断无法二分

现象：
- 日志出现 `段 N 截断但无法继续二分（input=... 字符）→ 回退原文`。
- 常见于 1KB 左右的小段：输出被模型自报 `finish_reason=length`，或被行数比例启发式判定为疑似截断，但段落继续二分后子段会低于安全下限。

处理策略：
- 长段仍优先递归二分精修，避免单次响应 token 上限导致尾部丢失。
- 短段无法二分时，先带 `retry_hint` 对同一输入重试一次，明确要求完整保留输入内容。
- 重试仍截断或调用失败时回退原文，并保留 `truncated=True` 与质量报告，避免截断输出进入最终文档。

## CodeLLMRefiner 整文件输出截断

现象：
- 代码模式日志出现 `CodeLLMRefiner 输出被 token 上限截断（finish_reason=length, raw_len=0...）`。
- 即使显式调大 `max_tokens`，provider 也可能因为上下文/输出预算直接返回空内容并标记 `length`。
- 整个 `SourceFile` 回退原文会导致大块代码完全没有获得字符级 LLM 修复。

处理策略：
- `refine` 模式不要整文件硬顶 token；按行和字符数切成小块，每块独立调用 LLM。
- 每个 chunk 仍强制行数守恒；单个 chunk 截断、JSON 解析失败或行数变化时，只回退该 chunk，后续 chunk 继续处理。
- 汇总时拼回全部 chunk，并保留 `code.refine.chunked=N`、`code.refine.chunk_truncated=i` 等 flags 供质量报告和前端审查。
- `rewrite` 模式允许重排行，不做自动切块；需要依赖诊断窗口或人工小范围修复。

## LLM 误把重叠拍照页整页删除

现象：
- 产物中出现 `<!-- 本页内容与上一页完全重复，已去除 -->`。
- 原图只是拍照重叠：后一页与前一页有相同图表或段落，但仍包含新增列表项、后续段落或独立图片引用。

典型现场：
- `page07966.JPG` 与 `page07967.JPG` 都包含 SHL 结构图的一部分，但 `page07966.JPG` 仍有“概述”页下半部分与第 1/2 条内容，`page07967.JPG` 继续到第 3/4 条和后续段落；二者不是整页重复。

处理策略：
- prompt 明确 `![](images/0.jpg)` 与 HTML `<img src="...">` 图片占位符都必须保留。
- 禁止 LLM 用“本页内容与上一页完全重复，已去除”这类解释性注释替代页面内容。
- Pipeline 段级精修后检测此类注释，带 `retry_hint` 重试一次；重试仍出现或调用失败时回退原段并标记 `truncated=True`，避免坏结果写入 LLM 缓存。

## 代码模式不应耦合具体 OCR 引擎

现象：
- `code.enable=true` 时，如果 API 或全局配置强制改写 PaddleOCR 专用参数，会破坏“前端可任意选择 OCR 引擎”的设计。
- 如果所选 OCR 引擎未填充 `PageOCR.text_lines`，代码模式可能静默跳过全部页面，最终生成空的代码产物。
- 前端若只提交 `code.enable=true` 而没有为 PaddleOCR 显式提交 `paddle_pipeline="basic"`，
  后端会沿用默认 `vl` pipeline；PaddleOCR-VL 输出 markdown / layout 块，不输出
  `text_lines`，最终报“当前任务未获得任何行级 OCR 输出”。

处理策略：
- 代码模式只依赖 `PageOCR.text_lines` 这个 OCR 抽象产物，不在 API 或配置层强制切换 provider 或 provider 专用 pipeline。
- OCR 引擎可以自由实现行级输出；未实现时，代码模式应明确报错，提示当前引擎缺少行级 bbox 能力。
- retry/resume 必须保留 `CodeRestoreConfig`；历史任务若因旧 bug 丢失 code 快照，可从 `files-index.json` 或 `files/` 代码模式产物做最小兼容推断。
- 前端默认 PaddleOCR + 代码模式时，应把用户意图翻译成显式请求级 OCR 覆盖：
  `ocr.model="paddle-ocr/ppocr-v4"` + `ocr.paddle_pipeline="basic"`；API schema 必须接收该字段，
  否则 Pydantic 会丢掉前端意图。

## 代码模式质量不能只看归并文件数量

现象：
- 一批 IDE 代码照片最终可能只归并出少量源文件，看起来像“过度归并”。
- 如果真实项目里存在接近 3000 行的大文件，多页、多图合并到同一源文件是合理结果。
- 反过来，即使文件数量看似合理，也可能存在路径 OCR 污染、UI 噪声混入、跨栏错归或 LLM 截断。

处理策略：
- `merged_pages` 只能作为规模信息或风险信号，不能单独作为失败条件。
- 分组质量应结合路径候选置信度、行号区间连续性、column 来源、gap 折叠、语法/编译诊断和 UI 噪声命中判断。
- 大文件允许合并很多页，但每个 column segment 应保留来源页、行号范围、路径候选和 flags，供质量报告和前端 review 审计。
- 参考源码匹配只能作为可选增强，不应把代码模式绑定到 示例、C/C++ 或任何固定项目结构。

## 代码语法诊断不能只停在首个错误

现象：
- `ast.parse`、`node --check`、`gcc/g++ -fsyntax-only` 等解析/编译工具遇到严重语法错误时，可能只返回第一处错误。
- `#include` 缺失头文件属于依赖错误，但会阻挡 C/C++ 编译器继续暴露后续 OCR 造成的真实语法错误。
- 某些 OCR 噪声（例如代码表达式里的 `二`、孤立 `王`、全角括号/逗号）不一定能被编译器走到；如果前面有依赖或语义错误，纯编译器诊断会漏标。
- 代码模式前端依赖 `diagnostic.items` 渲染红色波浪线；如果后端只产出第一条 item，后续独立 OCR 语法错误不会被标注。

处理策略：
- 诊断器第一次发现语法错误后，只在临时副本中屏蔽已定位的语法错误行，重新运行同一解析器/工具，继续收集后续独立语法错误。
- 对缺失 include 这类 dependency，不能只按行屏蔽；应从编译器错误中提取缺失头文件路径，在临时 include root 里生成 stub header，并把该 root 追加到编译器 `-I` 后继续复诊断。
- 对 C/C++、Python、JS/TS、Go、Rust 等代码文本额外做轻量词法扫描，忽略注释和字符串，标出代码区中的 CJK / 全角字符 OCR 噪声，避免被编译器短路吞掉。
- Python 复诊断屏蔽疑似复合语句头时，同时清空其缩进 suite，避免残留缩进错误再次阻塞后续顶层错误。
- 复诊断不得修改用户文件；最多迭代有限次数，无法恢复时保留已收集的诊断。
- 前端继续以 `diagnostic.items` 为唯一行级标注源，多条 syntax item 应分别渲染红色波浪线和 tooltip；编辑态实时诊断结果可被用户按条接受/隐藏。

## 多子文档预览源图过滤导致滚动同步失效

现象：
- 文档边界检测把同一输入目录拆成多个子文档后，部分子文档左侧源图列表为空或不含当前文档页，右侧 Markdown 滚动无法同步到源图。
- 根因是前端把 `doc_dir` 同时当成输出目录和输入源图目录前缀；边界拆分产生的 `doc_dir` 是输出标题目录，不一定存在于 `/source-images` 返回的相对路径中。

处理策略：
- 优先从当前子文档 Markdown 的 `<!-- page: ... -->` 标记提取页集合，用页文件名匹配源图。
- `doc_dir` 只作为输入目录前缀线索；当 `doc_dir` 形如 `输入子目录/输出标题` 时，逐级剥掉尾部标题目录后再匹配。
- 没有 page marker 时才退回旧的 `doc_dir/` 前缀过滤，保持输入子目录拆分场景兼容。

## Renderer 图片引用正则不支持中文/Unicode 文件名（PPT 模式 S4 暴露）

现象：
- `output/renderer.py::_rewrite_and_copy_images` 的 markdown / HTML 图片正则原用 `[A-Za-z0-9_.]+` 匹配 `{stem}_OCR` 的 stem，只覆盖 ASCII 文件名。
- 输入照片文件名含中文（如 `微信图片_20260428...`）时 stem 含中文字符，正则不匹配 → 图片引用既不重写也不复制，`document.md` 的 `<img src>` 仍指向不存在的相对路径，`images/` 为空。
- 文档模式因测试数据多为 ASCII 文件名未暴露；PPT 模式 S4 用真实中文文件名照片时显现。

处理策略：
- stem 字符类从 `[A-Za-z0-9_.]+` 放宽为排除路径分隔/定界符：markdown 用 `[^/)]+`（排除 `/` `)`），HTML 用 `[^/]+`（排除 `/`），靠后续 `_OCR/images/` 与 `)` / `"` 锚定回溯，兼容任意 Unicode 文件名。
- 图片引用加 OCR 目录前缀的逻辑抽到 `processing/dedup.py::rewrite_image_refs_to_ocr_dir`（module 级 public），文档模式与 PPT 模式共用，避免规则分叉。
- 回归：tests/output + tests/pipeline 全通过；真实 3 页中文文件名照片 → `document.md` + 5 张 `{中文stem}_N.jpg` 正确复制。

## 统一 LLM 精修开关不能连带关掉 PII 实体检测

现象：
- 引入统一 `LLMConfig.enable_refine` 后，把开关拦截点放进 `_get_refiner`（`enable_refine=False` → 返回 None），会同时关掉**非精修**用途的 LLM 客户端——`_delayed_pii_detect`（文档模式人名/机构名检测）与 `_redact_code_headers`（代码头脱敏）都经 `_get_refiner` 取 refiner。
- 后果：用户“关精修 + 开脱敏”时，正则只兜手机/邮箱/身份证/银行卡，人名/机构名检测被精修开关连带关掉，泄漏到云端 LLM 与输出。属隐私回归（max-effort review 第二轮 #1）。

处理策略：
- 区分“精修策略”与“LLM 客户端能力”：`_get_refiner(llm, *, for_refine=True)`。精修调用点用默认 `True`（受 `enable_refine` 约束）；PII 等非精修用途显式传 `for_refine=False`（只看 `model` 是否配置，不看 `enable_refine`）。
- `initialize` 预建 `self._refiner` 不再以 `enable_refine` 为前置条件——它是客户端能力，关精修也要可用。
- 代码模式 `base_refiner` 用 `for_refine=False` 取（供 PII 头脱敏），字符级精修 / pre-refine 诊断两段另按 `enable_refine` 各自 gate。
- 通则：凡新增“统一开关”集中拦截某个多用途方法时，先盘点该方法的全部调用点用途，避免把无关功能一并关停。

## PPT 按页精修的 prompt / 角点 / 退化四边形（review 第二轮其余三项）

现象与对策详见 `ppt-mode.md` §15「max-effort code-review 第二轮修复」。要点：
- PPT 每页独立 → 用 `SLIDE_REFINE_SYSTEM_PROMPT`（**不跨页去重**，否则误删合理重复的标题/页脚），缓存走独立 `slide` 命名空间（按 slide prompt 指纹，不与文档分段缓存串味）。
- `_order_corners` 改“按 y 排序分上下、组内按 x 分左右”（取代极角 + x+y 锚点）：旋转/强倾斜下标号仍正确，回归断言标号正确而非仅 4 角互异。
- `rectify` 退化 sliver（任一边 < `_MIN_RECTIFIED_SIDE_PX=16`）回退整张原图，不产 1×N 竹签图喂坏 OCR。

已清理（2026-06-04，原暂缓 5 项全做）：
- 关精修时改报 `progress.pptPagePlain`「处理第 X 页」（refining 分支控制），不再误报「精修第 X 页」。
- 删除从不发射的死 i18n 键 `progress.pptDone`（三语）。
- `_ocr_config_for_code_mode` / `_ocr_config_for_ppt_mode` 合一为参数化 `_ocr_config_force_pipeline(ocr, default_ocr, pipeline_name)`，两薄封装分别传 basic / vl。
- 新增 `TaskManager._retry_ppt_config`（对称 `_retry_code_config`）：retry/resume 时 `task.ppt` 为空则用 `output_dir/.rectified/` 推断回 PPT 模式，不再静默退回文档模式。
- `_ppt_pipeline` 段级缓存 `enabled=enable_cache and (refiner is not None)`：关精修时禁用缓存、不再建空 `.llm_cache/` 目录。

## Vite 代理报错刷屏 + 后端后起前端卡死需重启（review 第三轮，用户报）

现象：后端未就绪时先启动前端 → Vite 终端刷一串 `[vite] http proxy error ... ECONNREFUSED`，且界面长期不可用、必须重启前端才能开始新任务。

根因：
- Vite 8 对 HTTP 代理错误**无条件**打日志（`node.js` proxyMiddleware；自定义 `server.proxy.configure` 的 error 处理器改不掉，且 Vite 已自行返回 502，浏览器侧拿到的是 502 而非 "Failed to fetch"）——所以改 `vite.config.ts` 无法静音该日志。
- 前端 `listGpus` / `getOcrStatus`（`TaskForm`）与 `listTasks`（`SidebarTaskList`）三处挂载请求只打一次、失败 `catch {}` 静默放弃，后端随后就绪也不再拉取 → 必须重启前端。

处理策略：
- 新增 `frontend/src/lib/retry.ts::retryUntilSuccess(task, delaysMs)`：退避重试（默认 1/2/4/8s 末值循环）直到任务成功（不抛异常）即停，effect 卸载时取消挂起重试。
- 三处挂载 effect 改走它（`fetchTasks` 返回 bool 供重试判定）→ 后端就绪后界面自动恢复、无需重启；代理报错从「瞬间一串 + 永久死」变「少量间隔重试 + 就绪即停」。
- 注意：Vite 代理日志本身无法在 `vite.config.ts` 静音，只能靠降低请求频率缓解。

## 实体（人名/机构名）脱敏未覆盖主精修与输出（全链路，已闭环 2026-06-04）

现象（max-effort review #1 复核更正）：开 `redact_person_name`/`redact_org_name` 时，结构化 PII（手机/邮箱/身份证/银行卡）由 producer 正则在入云端前对全模式（含 PPT）脱敏；但 LLM 实体（人名/机构名）词表 `_delayed_pii_detect` 只用于文档模式 gap-fill 重 OCR 片段——**主分段精修 / PPT 按页精修 / 最终输出都未用它**，人名/机构名原样进云端 + 留输出。非 PPT 独有、非某次 diff 引入，属全链路既有缺口。

处理（**已实现 2026-06-04，设计与落地见 `backend/privacy.md §9`**）：检测沿用所配置 refiner（积累 N 页后建一次 lexicon），lexicon 应用到 doc 主分段精修入参 + PPT 每页精修入参 + 最终输出兜底（早窗口靠输出兜底覆盖），保持文档流式。约束：LLM 实体检测本身要把文本送 LLM，检测调用仍上云一次——要名字完全不出本机需配 local provider。回归：`tests/pipeline/test_entity_redaction.py`（6 用例）。

## block_cloud_on_detect_failure 失效，检测失败仍外发实体（#10，已修复 2026-06-04）

现象：
- `PIIConfig.block_cloud_on_detect_failure`（默认 True，语义"实体检测失败就不外发"）全 backend 零读取、声明即死代码。
- LLM 实体检测抛错时 `_detect_entities` 仅 log 并返回 `entity_lexicon=None`，下游照常把含真实人名/机构名的整段送云端精修 → 隐私 fail-closed 承诺从未兑现。

根因：`entity_lexicon is None` 一个信号混淆了三种情形（未开脱敏 / 早窗口未检测 / 检测失败），下游无法区分"失败"并据此阻断。

处理策略：
- 新增 `Pipeline._should_block_cloud(lexicon, pii_cfg)`：仅"开 PII + 要求人名/机构名脱敏 + 检测返回 None（失败）+ flag 为真"时返回 True；检测成功（含查无实体的空词表）/未开脱敏/早窗口均不误判。
- 文档模式 `_stream_process`、PPT 模式 `_ppt_pipeline` 的检测点命中失败即置 `refiner=None`（后续段/页退原文）；`_finalize_single_doc` 加 keyword-only `block_cloud`，为真时跳过 gap fill 与 final refine 两处云端调用。
- 注意：实体检测调用本身仍要把文本送 LLM；本修复阻断的是检测失败后的"后续精修外发"。回归：`test_entity_redaction.py` 的 `_should_block_cloud` 四分支 + PPT fail-closed 阻断/flag 关不阻断。

## OCR 生产者任务在中断时不被取消（#8，已修复 2026-06-04）

现象：
- `_stream_pipeline` 旧 `finally: await ocr_task` 前缺 `ocr_task.cancel()`，`page_queue` 又是无界队列。
- 消费者提前退出（用户取消 / shutdown / 内部异常）后，生产者仍持 `gpu_lock` 把剩余图全 OCR 完才结束 → `manager.shutdown()` 阻塞数分钟、取消形同失效、GPU 空转；CancelledError 在途时还可能就地再抛而遗弃仍在跑的生产者。

处理策略：
- `try` 改 `try/except/finally`：`except BaseException` 先 `ocr_task.cancel()` 再 `suppress(CancelledError, Exception)` await，吞清理异常、保留消费者原异常 `raise`。
- 成功路径保留 try 外 `await ocr_task`：生产者已在自身 finally `put(None)` 收尾，这里 await 让其真实异常（如某页 OCR 失败）浮现为任务失败，而非静默截断文档。
- 回归：`tests/pipeline/test_producer_cancel.py`（消费者抛错 → 生产者被取消、未跑完所有图、原异常上抛）。

## 熔断器半开探测被取消时永久卡死（#9，已修复 2026-06-04）

现象：
- `_call_llm` 用 `except Exception` 捕获，`CancelledError`（BaseException）不被捕获。
- HALF_OPEN 探测调用被取消（用户取消任务 / shutdown / wait_for 超时）时 `on_success`/`on_failure` 都不执行，`before_call` 设的 `_probe_in_flight` 永久泄漏 → 该 `(model, api_base)` 全局单例熔断器此后每次调用 fail-fast，整进程段级精修退化为原文直到重启。

处理策略：
- 新增 `LLMCircuitBreaker.on_probe_aborted`：清除 `_probe_in_flight`（不计成功/失败、不触发退避），状态不变，下次调用可重新探测。
- `_call_llm` 把 rate_limit 获取 + api_call 整段包进 `try`，新增 `except asyncio.CancelledError` 调用 `on_probe_aborted` 后原样上抛，覆盖 `before_call` 之后到 `on_success/on_failure` 之间的全部取消窗口。
- 回归：`tests/llm/test_circuit_breaker.py` 的 `TestProbeCancellation`（取消后可重探/恢复 CLOSED；并以"不清除即第二次 before_call fail-fast"佐证修复必要）。

## 历史任务还原被单条损坏行中断（#14，已修复 2026-06-04）

现象：
- `load_persisted_tasks` 的 `try/except` 只裹 `get_results`；`TaskStatus(row.status)`、`get_task`（内部 `LLMConfig.model_validate_json`）、`datetime.fromisoformat(row.created_at)` 全裸奔。
- 一条旧版/损坏行（status 不在枚举、config JSON 损坏、时间格式异常）抛 `ValueError` 冒出分页 `while` → 该行之后的所有任务重启后从 UI 静默消失。

处理策略：
- 把单条 row 的解析（`get_task` → `Task(...)`）整体包进 `try/except`，失败 `logger.exception` + `continue` 跳过坏行，不中断整个分页加载。
- 回归：`tests/pipeline/test_task_manager.py::TestLoadPersistedResilience`（坏行 `created_at` 更新 → DESC 先处理；断言好行仍装回、坏行被跳过）。

## 结果落库非原子，崩溃留下"完成但零结果"（#15，已修复 2026-06-04）

现象：
- `_persist_results` 先 `update_status('completed')`（commit）再 `insert_results`（commit），两次独立事务。
- 两 commit 之间进程被杀 → `tasks` 行已是终态、`task_results` 为空；`_recover_interrupted` 只修 pending/processing，无法补救 → 永久"完成但无结果可下载"。

处理策略：
- `database` 新增 `complete_task_with_results`：单事务内 UPDATE 状态 + 批量 INSERT 结果，一次 commit（崩溃落在 commit 前 → 状态仍是 processing，可被 recover 修复）。
- `_persist_results` 改走该原子方法；抽 `_normalize_results` 供 `insert_results` 与新方法复用。
- 注意：单连接 aiosqlite 下并发写仍可能互相提交（更深的隔离问题，不在本条范围）。回归：`tests/persistence/test_database.py`（原子写 / 空结果各 1）。

## 代码模式不尊重 block_cloud_on_detect_failure（#25 = #10 残留，已修复 2026-06-04）

现象：
- #10 只让文档 / PPT 模式 fail-closed；自查发现**代码模式**仍漏。
- `_redact_code_headers` 实体检测失败时只 log、退化成"仅 regex + 自定义词"（header 人名/机构名未脱），且**不向上游返回失败信号**；随后 `_code_pipeline` 把 `src.merged_text` 经 `code_refine` / `code_repair` / `code_audit` 送云端，无 fail-closed 闸门 → 开 PII + 检测失败 + flag 真时仍外发含真实姓名的代码头。

处理策略：
- `_redact_code_headers` 改返回 `block_cloud: bool`（实体检测**已尝试且失败** + `block_cloud_on_detect_failure` 为真）。
- `_code_pipeline` 在 `refine_on` 闸门加 `and not pii_block_cloud`，跳过整段 refine / repair / audit 云端循环（退化为不精修的本地输出）。
- 回归：`tests/pipeline/test_code_pii_header.py::TestRedactCodeHeadersFailClosed`（检测抛错→True / flag 关→False / 检测成功→False / 无 refiner→False）。

## shutdown 全量清理擦掉已持久化任务源图（#11，已修复 2026-06-04）

现象：
- `cleanup_all_sessions`（app shutdown 调用）无条件 `shutil.rmtree` 每个 upload_dir，无引用跳过；而 TTL 路径 `cleanup_expired_sessions` 专门跳过被任务引用的目录。
- 完成任务的 `image_dir` 指向某 upload_dir → 优雅重启时被删 → 重启后 resume/retry 对空目录跑、源图预览 404（重新引入 2026-04-23 修过的"烂图 bug"）。

处理策略：
- `cleanup_all_sessions(referenced=None)` 加可选引用集合，命中的 upload_dir 跳过不删；`None` 时清全部（旧行为，测试用）。
- `app.py` shutdown 传 `await manager.collect_referenced_image_dirs()`（含内存 + DB 全部终态任务的 image_dir）。
- 回归：`tests/api/test_upload.py::TestCleanup::test_cleanup_all_skips_referenced_dir`。

## 终结进度帧丢失致 WS 客户端永久阻塞（#16，已修复 2026-06-04）

现象：
- `publish_progress` 仅当此刻有订阅者才广播；终结帧（completed/failed）只发一次、不缓存；`subscribe_progress` 建空 `Queue(maxsize=1)`、不回灌当前状态。
- 客户端若在终结帧 publish 之后才订阅（小/缓存任务在 WS 连上前就完成）→ `await q.get()` 再无后续 publish，永久阻塞（WS 循环的状态重检在 `q.get()` 之后救不了）。

处理策略：
- `subscribe_progress` 订阅时若 `task.progress` 非空，立即 `put_nowait` 回灌一帧，让晚到订阅者立刻拿到最新状态（含终态）并据此退出。
- 回归：`tests/pipeline/test_task_manager.py::TestProgressPubSub::test_subscribe_seeds_current_progress_after_terminal`。

## DeepSeek init 进度并发读同一 stderr 致失效（#18，已修复 2026-06-04）

现象：
- 基类 `_start_worker_process` 起 stderr drain 读 `process.stderr`；DeepSeek `_send_init_command` 又起协程 `_stream_stderr_progress` 读同一 `StreamReader`。
- `StreamReader` 不允许并发 `readline()` → RuntimeError，init 进度（vLLM 加载 30-120s）静默失效；若调度反转 drain 死亡 → stderr pipe 写满、worker 阻塞挂死。

处理策略：
- 单一读者：`_drain_stream_to_logger` 加可选逐行 `on_line` 回调，`_start_worker_process` 传 `self._dispatch_stderr_line`（转当前 `_stderr_line_hook`）。
- `_send_init_command` 不再起第二个读者，装一个解析进度的逐行 hook、init 结束（成功/异常/取消）即在 finally 摘除；删死代码 `_stream_stderr_progress`。
- 回归：`tests/ocr/test_worker_transmission.py::test_drain_routes_each_line_to_on_line`。

## DeepSeek 批量 OCR 静默丢页致下游索引错位（#19，已修复 2026-06-04）

现象：
- `_send_ocr_batch_all` 按 `enumerate(items_raw)` 建 results，worker 返回项数 < 请求 chunk 时缺失页被悄悄省略；`ocr_batch` 末尾 `[results[p] for p in image_paths if p in results]` 又把缺页静默丢掉。
- 结果：返回的 PageOCR 页列比输入短，下游页索引/去重整体错位。

处理策略：
- `_send_ocr_batch_all` 加 `len(results)==len(chunk)` 硬校验，缺页抛 RuntimeError（含缺失页名）。
- `ocr_batch` 末尾改为"缺页即抛"的兜底防线，不再 `if p in results` 静默过滤。
- 回归：`tests/ocr/test_worker_transmission.py::test_batch_raises_on_missing_pages`。

## resync 复用 OCR 超时（#17，重判为非 bug / wontfix 已关闭 2026-06-04）

现象（原审）：某页 OCR 被取消后 `_pending_resync` 置位，下次 `ocr()` 在 `_resync_if_needed` 用 `_get_timeout()`（300s/600s）drain 残留，worker 假死时下一页冻结数分钟。

重判（2026-06-04）：`_pending_resync` 仅在"命令已发、worker 正在处理该 OCR"被取消时置位，残留响应会在 worker 完成那次 OCR 时到达。原建议的"短超时即 restart"会**过早重启正在干活的 worker**、丢弃热进程，是错的。当前"用 OCR 超时 drain、超时才 restart"是可辩护的权衡（复用热 worker vs restart reload 成本）。

处理策略：倾向 wontfix；若要改，只加一个可配置的中等 resync 超时（默认不变），不强制短超时。已在 GitHub issue #17 记录重判；用户采纳 wontfix，issue 已关闭（not planned）。

## LLM 实体输出未消毒即全局替换致整篇打碎（#13，已修复 2026-06-04）

现象：
- 检测侧 `cloud.py::detect_pii_entities` 用 `list(data.get("person_names", []))`：LLM 偶发把字段写成裸字符串 `"Alice"`，`list("Alice")` → `['A','l','i','c','e']`。
- 替换侧 `redactor.py::_replace_entities` 仅 `if not name: continue`，无最小长度/纯标点校验，对每名全局 `str.replace`。
- 后果：文档里每个 a/l/i/c/e 被替成占位符；或 LLM 幻觉单字"的"/"人"被全篇替换 → 整篇打碎，且发往云端并作为最终输出，不可逆。

处理策略：
- 检测侧新增 `_coerce_str_list`：非 list 一律丢弃（裸字符串视为字段缺失），list 内仅留去空格后非空 `str`；顶层非 dict 抛 `RuntimeError`（fail-closed，交由调用方决定是否阻断云端）。
- 替换侧 `_is_safe_entity`：长度 ≥2 且含至少一个 alnum 字符（`str.isalnum()` 对中文返回 True，借此排除纯标点）；异常高频(>50)/超长(>64)实体记 WARNING 仍执行。
- 回归：`tests/llm/test_cloud_truncation.py::TestCoerceStrList` + `TestDetectPiiEntitiesJsonParse`（裸串/混类型/顶层数组）；`tests/privacy/test_redactor.py::TestIsSafeEntity` + `TestEntityReplaceSafety`。

## heading 去重子序列总和误删同名异容节（#20，已修复 2026-06-04）

现象：
- `_should_merge` 的 `truncated_prefix` 路径：`match_size = sum(b.size for b in m2.get_matching_blocks())` 把离散匹配块求和，是有序子序列长度而非连续子串。
- 两个同标题节（如 `## 参数`）内容确实不同，但短节字符作为有序子序列散落长节里（短文本+共享词时极易达 90%）→ `asymm ≥ 0.9` → 短节被静默删除（reason 误标 `truncated_prefix`）。属"去重删了非重复项"。

处理策略：
- 保留 0.9 子序列阈值，追加连续性闸门 `contiguous_anchor_ratio=0.5`：用 `find_longest_match().size / len(short)` 要求存在一段足够长的【连续】匹配块作截断锚。
- 实测：真截断（短=长前缀+少量 OCR 噪声尾）连续块占比 0.727；散点子序列仅 0.083；0.5 闸门两侧裕度充足。
- 回归：`tests/processing/test_heading_dedup.py::TestDifferentSectionsKept::test_scattered_subsequence_not_merged`（散点子序列两节都保留），并复核既有 `test_truncated_then_complete_keeps_complete` 仍合并。

## 取消 vs 完成竞态致任务终态错乱（#12，已修复 2026-06-04）

现象：
- `cancel_task` 检查 `status ∈ {PENDING, PROCESSING}` 后于锁内直接写 FAILED；`run_task` 成功路径写 COMPLETED。两路独立无守卫地覆盖终态。
- `asyncio.Lock` 空闲 acquire 走不挂起快路径，`bg.cancel()` 的 CancelledError 不在此投递；`process_tree` 刚返回与 `cancel_task` 并发 → "已取消的任务最终 COMPLETED"或"已完成的任务被标 FAILED"（结果已落库却显示失败）。

处理策略：
- 引入单一真相源 `_finalize(task_id, new_status, *, results, error)`：锁内重检，若已是终态（COMPLETED/FAILED）则放弃返回 False，否则原子应用并返回 True。
- run_task 的成功 / 部分失败 / CancelledError / 未预期异常四路 + cancel_task 全部改走 `_finalize`，先到终态者赢；持久化/广播仅在本次抢到终态时执行。
- cancel_task 在 `_finalize` 返回 False 后重读状态：COMPLETED → 取消失败返回错误；FAILED → 取消已生效返回成功。
- 抽 `_handle_unexpected_failure` 降低 run_task 圈复杂度（C901）。
- 回归：`tests/pipeline/test_task_manager.py::TestFinalizeRace`。

## 代码模式按 OCR 行号排序致误读重排 / 崩溃（#21，已修复 2026-06-04）

现象：
- `code_assembly.py:144` `sorted(line_no_lines, key=lambda ln: int(ln.text.strip()))`：行号 88 被 OCR 读成 8 → 该行排到列顶并以 line_no=8 进 ledger / 合并，制造大段虚假缺号。
- 复核：`NUMERIC_RE=^\d{1,4}$`（两端锚定）已保证行号是纯数字，`int()` 在此路径不会抛 ValueError（issue 的崩溃描述属过度推断）；真正未修的是误读值重排。

处理策略：
- 新增 `_ordered_line_numbers`：① 排序键改 `bbox[1]`（y_top）——照片垂直顺序是物理真相（与 `code_line_ledger._y_monotonic_outliers` 同一前提），误读不重排；② `int()` 包 try/except 防御兜底（日后放宽正则不崩）；③ 单调性修正——读数 ≤ 前一已接受值即误读离群，改 `prev+1` 推断并标 `is_inferred_line_no`。
- `anchor.num_range` 由同批 OCR 读数派生（可能含离群点），仅用作首行无前值时的下界兜底，不作硬边界。
- 回归：`tests/processing/test_code_assembly.py::test_ordered_line_numbers_sorts_by_y_not_misread_value` + `test_ordered_line_numbers_handles_unparsable`。

## 服务端源图预览跟随 symlink 后越界校验致全 404（#22，已修复 2026-06-04）

现象：
- `get_source_image` 用 `(img_dir/filename).resolve()` 跟随软链后再 `is_relative_to(img_dir.resolve())`；但 `_stage_files` 用 `symlink_to` 把服务端源文件软链进 stage 目录、指向外部真实路径。
- 任何"服务器目录"建的任务：`list_source_images` 能列出软链，但 `get_source_image` resolve 到外部路径 → 包含校验 False → `IMAGE_NOT_FOUND`，整个服务端源图预览不可用。

处理策略：
- 不对拼接路径 `resolve()`；改对【未跟随 symlink】的词法拼接路径 `img_dir/filename` 做 `is_relative_to` 越界校验（filename 上游已禁 `..` 与前导 `/`），再用 `is_file()` 跟随软链确认目标存在。
- stage 内软链均由本服务 `_stage_files` 创建、指向用户显式选定且已校验的图片，放行其目标安全。
- 回归：`tests/api/test_source_images.py::TestGetSourceImage::test_serves_symlinked_staged_image`（既有 `..`/绝对路径穿越用例仍 400）。

## 新建任务完成后预览空白，需切历史记录再回来才刷出（已修复 2026-06-05）

现象：
- 新建任务页任务跑完，结果区 `TaskResult` 卡在"暂无可用结果"；进一次历史记录详情再切回新建任务页，预览才正常出现。

根因（前端时序竞态）：
- `TaskResult` 刻意采用"挂载时一次性吃 props"模式（`useState(() => [...initialResults])`），靠 App 的 `key={taskId}` 重挂载来刷新；`taskId` 在结果到达前就已确定、不会变。
- 但 `useTaskRunner` 完成路径顺序是「先 `setStatus("completed")` 再 `await fetchResult`」：状态一翻，App 立即挂载 `TaskResult`，此刻 `allResults` 仍是 `[]` → 组件吃到空数组；随后结果到达，`taskId` 未变不重挂载 → 永远停在空态。
- 切到历史详情会让 `isCreateMode` 翻 false 卸载 `TaskResult`，切回时以已填充的 `allResults` 重挂载 → 误以为"绕一圈能修好"。

处理策略：
- `useTaskRunner` 两条完成路径（轮询 `handlePollResponse` + WS 关闭兜底）统一改为「先 `await fetchResult(tid)` 再 `setStatus("completed")`」，保证状态翻 completed 时 `allResults` 已就绪，`TaskResult` 首挂载即拿到数据。
- 历史详情 `TaskDetail` 走 `useTaskProgress` + 自持 reactive `docResults`（`getTaskResults → setDocResults` 直传），本就不受该竞态影响，无需改动。
- 通用经验：凡"挂载时一次性吃 props + key 重挂载"的展示组件，其数据必须在「门控状态翻终态之前」就位，否则同 key 下的迟到数据永远进不来。

## 文档/PPT 预览左右同步滚动在水合后失效（已修复 2026-06-05）

现象：
- 文档模式、PPT 模式的"原图 ↔ markdown"左右同步滚动不跟随；代码模式同步滚动正常。

根因：
- 文档/PPT 同步滚动靠 markdown 里的 `<!-- page: 文件名 -->` 标记（前端 `injectPageAnchors`
  转成右栏 `[data-page]` 锚点，`useScrollSync` 按它对齐）。
- `Renderer.render` 按设计把磁盘 `document.md` 剥除 marker（下载/交付版），带 marker 的"预览版"
  只通过返回值留在内存（`PipelineResult.markdown`）。
- 任务一旦从 DB **水合**（后端重启 / 看历史任务），`task_manager.load_persisted_tasks` 用
  `_read_text_or_empty(document.md)` 从磁盘**剥除版**重读 markdown → 右栏零锚点 →
  `getCenterPagePosition` 返回 undefined → 同步滚动静默失效。
- 代码模式锚点来自 `files-index.json` 的 `source_page_ranges`（落盘持久化），水合后仍在，
  故只有文档/PPT 中招。证据链：`debug/merged_raw.md`(10) → `reassembled.md`(9) →
  `final_refined.md`(9) → `document.md`(0)，marker 在落盘那步被剥光。

处理策略：
- `Renderer.render` 额外落一份带 marker 的 sidecar `.document.anchored.md`（PPT 经同一 render
  路径自动覆盖）；`document.md` 仍剥除版。
- 水合新增 `_read_hydration_markdown`：优先读 sidecar（带锚点），缺失/空时回退 `document.md`。
- 编辑保存 `update_result_markdown` 同步刷新 sidecar，防"已渲染文档的旧 sidecar 未更新"。
- 下载 zip / assets 接口均不含 sidecar（dot 前缀 + 白名单只放 `document.md`），下载保持干净。
- 老任务（无 sidecar）需重跑生成；或用 `debug/final_refined.md` 经渲染器同款图片重写回填
  sidecar，并以 `strip_page_markers(sidecar)==document.md` 为逐字符等价守卫（不等则跳过重跑）。

## PII 脱敏链路审计：上云端精修前能拦什么（2026-06-06）

结论（"上 LLM 精修前是否脱敏"）：

| 类别 | 检测器 | 上云端精修前去掉？ |
|---|---|---|
| 手机/邮箱/身份证/银行卡 | regex | ✅ producer 逐页 `redact_regex_only`（`pipeline.py:1587`）入队前 |
| **密码/用户名/账号/token** | **regex（本次新增）** | ✅ 同上，走 `redact_structured_pii` step-0 凭据检测器 |
| 自定义敏感词 | 精确匹配 | ✅ 同上，连本地 debug/cache 都不留明文 |
| 人名/机构名 | 本地 NER `PIIGuard.detect_entities`（spaCy，S4 起；原云端 LLM `detect_pii_entities` 已于 S4 删除 2026-06-15） | ⚠️ 部分：**早窗口已修**（词表就绪前不送云端）；S4 起检测改本地 NER（`privacy/ner.py`），名字不出本机，不再有云端检测调用曝光 |

**已修 1（2026-06-06，commit 1e4a68a）**：新增凭据/token regex 检测器（label 锚定 KV +
URL 内联 `user:pass@` + sk-/ghp_/AKIA/JWT 已知格式），补上密码/用户名/账号/token 的空缺。
因在 producer 入队前的正则层执行，上云与落盘前即抹掉。偏向宁多勿漏，技术正文不误伤，
`redact_credential` 默认开可关。

**已修 2（2026-06-06，commit 51f0b38）——早窗口防泄漏**：开 PII 且要求实体脱敏时
（`_entity_redaction_pending`），实体词表就绪前只攒页不送云端精修，就绪后一次性追平
（文档 `_stream_process` 用 `try_extract` 追平；PPT `_ppt_pipeline` 用 pending 缓存 +
`_finish_page` 闭包追平）。代价是词表就绪前的"先攒后发"流式延迟。**附带收益**：分段送云端前
已脱敏 → `reassembled.md` / `final_refined.md` dump 与 `.llm_cache`（按 enable_cache，非
debug-gated）对早窗口段也不再留人名明文。红绿验证：`tests/pipeline/test_pii_early_window.py`。

**已修 3（2026-06-06，commit 21f72cc）——代码模式正文 PII**：`_redact_code_headers`
改名 `_redact_code_pii`，正文也脱敏——header 走 `redact_snippet`（regex + 实体 + 自定义词），
正文走 `redact_regex_only`（结构化 PII + 凭据/token + 自定义词，**不做实体脱敏**以保 import
路径 / namespace / 标识符）。实测硬编码 password/sk-token/URL 凭据/正文邮箱/电话均在送云端
refine/repair/audit 前脱掉，`Zhang_counter` 等 name-like 标识符不动。取舍：凭据 KV 在正文里
可能误伤 `password=<expr>` 右侧（`redact_credential` 可关）。回归：`test_code_pii_header.py`。

**未修遗留（已知，另排期）**：
1. **人名/机构名检测调用曝光（已于 S4 闭环 2026-06-15）**：原云端实体检测（`detect_pii_entities`）
   要把文本给 LLM 才能认（用云端 provider 时即外发一次）；早窗口段精修曝光已修，但检测调用本身仍上云。
   S4 起检测迁到本地 NER（`PIIGuard.detect_entities` → `privacy/ner.py` spaCy），名字不出本机，云端检测
   调用曝光已彻底消除——本条「彻底规避检测曝光需本地 NER」的补法已落地。
2. **代码模式正文里的人名/机构名**：正文只做 regex/凭据/自定义词脱敏，**不做实体脱敏**（实体检测
   会把变量名/namespace 当人名误替换，AGE-50）。故正文注释里的人名/机构名仍可能上云。header 的
   人名/机构名已脱。
3. **磁盘留底（landmine B，剩余）**：检测**输入**性质的 dump —— `debug/merged_raw.md` 与
   `debug/*_cleaned.md`（producer 输出，实体检测前）—— 仍含人名明文，但**仅默认 `debug=True`
   时落盘**（用户关 debug 即无此留底）。`reassembled/final_refined` 与 `.llm_cache` 已被「已修 2」
   清掉早窗口人名。彻底补法：PII 开启时关 debug 或对检测输入 dump 也延迟到脱敏后。

## OCR 退化重复行致 token 爆炸 + 文档尾页整段消失（已修复 2026-06-06）

**现象**：处理一份约 150 帧拍摄的内部文档（chromium 零拷贝优化，正文含 GDB backtrace /
内存 dump 截图）时，① 精修阶段烧掉 **28M+ token**，后端刷屏
`LLM 输出因 token 上限被截断（finish_reason=length）` + `段 6 截断递归到达上限 depth=3，回退到原文`；
② 原本 100+ 张插图的文档最终只剩 **6 页 12 张图**，尾页内容整段丢失。

**根因（单一）**：OCR 撞上内存 dump 的不可读字节串（`pui8Src=0x3fcc792000 "..."`）时产生
**退化重复幻觉**——把字节识别成 `wm`/`nt`/`mu`/`00` 这类 1–4 字符短单元，重复成百上千次，
清洗后仍是**单行 8093 字符**。这一行同时引爆两个症状：
- **token 爆炸**：喂给云端 LLM 精修 → 模型陷入同款 `wm` 重复生成直到 `finish_reason=length`
  截断 → 触发 `_maybe_retry_on_truncation` 二分递归（depth=3），多段叠加 → 28M token。
- **尾页消失**：含 giant line 的段精修时 LLM 把 token 耗在垃圾上、走不到段内后续的
  page marker 就截断；截断恢复未能完整保住尾部 → `reassembled.md`（= join(refined_results)）
  只剩 8 个 page marker（DSC07564–07570），而 `merged_raw.md` 仍有全部 152 个。
  逐级放大佐证：清洗 8093 → merged 28117 → reassembled 126069 字符（LLM 每过一手又多生成）。

**为何旧清洗漏掉**：`remove_repetitions` 只按**空行分段**比对相邻段落相似度（管不了单行内重复）；
`remove_garbage` 只删**连续非可读字符**（CJK/ASCII 字母数字属"可读"，明确保留）。`wm`/`nt` 全是
ASCII 字母 → 两道防线都放行。

**修复（commit 见下）**：`OCRCleaner` 新增 `collapse_degenerate_runs`，在逐页清洗**最前**就地
折叠短单元（≤4 字符）超长重复游程（`DEGENERATE_RUN_RE = (.{1,4}?)\1{8,}`，真正阈值由
`DEGENERATE_RUN_MIN_CHARS=60` 在回调按字符数把关），保留行首上下文 + 留可见折叠标记。
giant line 在进 merger/segmenter/refine 前消失 → 三个症状同源消除。
- **误伤守卫**：纯分隔符单元（`DEGENERATE_DIVIDER_CHARS = -=*#_~|+.` 与空白）的重复不折叠，
  保护 markdown 下划线 `====` / 代码 banner `####` / 分隔线 `----`；短于 60 字符的重复不动。
- **性能/安全**：反向引用游程线性匹配无灾难性回溯（实测 128K 退化行 → 47 字符仅 5.6ms，
  50K 随机串 / 56K 正常文档原样不动 < 6ms）。
- **实测**：真实垃圾页 `DSC07570_cleaned.md` 18217 → 2225 字符（降 88%），最长行 8093 → 227，
  dump 上下文 `...pui8Src=0x3fcc792000 "ntntnt…` 可读保留。
- **红绿验证**：`tests/processing/test_cleaner.py::TestCollapseDegenerateRuns`（7 例：真实 2 字符游程 /
  十六进制 0 / 阈值下不动 / markdown 分隔符保护 / 正文代码不动 / 不跨行 / clean() 端到端）。

**残留风险（未修，另排期）**：折叠只针对"短单元重复"。若未来出现**非重复**的超长单行
（如 50KB minified JS / base64 blob），仍可能触发同款"段截断吞尾页"。彻底兜底需在 segment 层
加按字符数硬切 + reassemble 阶段做"页 marker 数 merged_raw vs reassembled"守卫（缺失即从
merged_raw 补回尾部）。本文档场景里 giant line 唯一来源就是退化重复（次长行仅 229 字符），
故折叠已完全覆盖；硬切守卫留待真有非重复巨行需求时再做，避免过度工程。

**顺带发现并已修（user@host + 内部 URL 脱敏，2026-06-06）**：该样本正文里
`scp ... qiangming@30.21.162.200`（用户名@IP）与源 URL `aliyuque.antfin.com/theadiotsw/...`
（内部 Yuque + 作者 handle）属 PII，但 `user@host`（无密码）不被凭据 regex（`user:pass@host`）
命中、URL 作者 handle 也未脱。已在 `privacy/patterns.py` 新增两个结构化检测器并接入
`redact_structured_pii` 表驱动 steps（文档/代码模式上云前的 `redact_regex_only` 自动覆盖）：

- **`_HOST_TARGET_RE`（`redact_host`，默认开，占位 `[主机地址]`）**：脱 `user@IPv4` 与
  `user@单 label 主机名`，user（常含人名）一起脱。**只接 IP 与无点主机名**——带点 FQDN /
  邮箱域名（`user@a.b.com`）交邮箱步骤，主机名分支末尾 `(?![A-Za-z0-9.-])` lookahead 拦住
  FQDN 前缀，避免关掉 `redact_email` 时把 `user@domain.com` 误切成 `[主机地址].com`。
  邮箱 step 先于 host，吃掉带 TLD 的 `user@domain.tld`。
- **`_URL_LIKE_RE`（`redact_internal_url`，默认开，占位 `[内部链接]`）**：私有/回环 IP
  （`ipaddress.is_private/is_loopback`）的 URL **零配置即脱**；host 命中
  `sensitive_url_domains` 后缀（用户配 `antfin.com` 即覆盖语雀）的 URL 整条（含路径里的
  作者 handle / 文档 ID）脱。**非私有 / 非配置域名的公网链接（github/stackoverflow）原样保留**
  （回调 `m.group(0)`），不误伤。支持无 scheme 裸域名（OCR 文档常无 `http://`）。

误伤守卫验证：`@staticmethod`/`@提及`/`@types/node`/`config.json`/`v1.2.3` 全不动；邮箱仍走
邮箱步骤占位 `[邮箱]`。红绿：`tests/privacy/test_patterns.py::TestHostTargetRedaction`（6 例）
+ `TestInternalUrlRedaction`（8 例）。**遗留**：`user@FQDN` 在关 `redact_email` 时既不被邮箱也
不被 host 脱（边缘，用户主动关 email 即选择保留邮箱形态）；裸公网 IP（非 user@、非 URL）不脱。

## 编辑模式（Tiptap）长文档只显示第一屏、无法下滑（2026-06-09，已修）

**现象**：编辑模式只能显示/编辑文档开头一屏，鼠标无法向下滚动看后续内容。用户在 PPT 模式首次发现。

**与模式无关**：文档模式与 PPT 模式编辑共用同一组件（`DocCodePreview` → `MarkdownWysiwygEditor`）
与同一份 CSS，编辑器里**没有任何按模式的分支**。触发条件是**编辑器渲染高度 > 可视区（~80vh）即被裁**，
与是文档还是 PPT、正文多长无关——论文本量文档模式通常**更长**，只会更早触发。之所以在 PPT 先暴露：
**预览**路径（`.markdown-preview`）把 `overflow-y:auto` 直接挂在滚动元素上、无包裹层，两种模式都正常滚；
**只有编辑器**有下面的包裹层缺陷，所以"谁先去编辑一篇超过一屏的文档"就先撞上，恰好是 PPT。PPT 即便正文
稀疏也易超一屏，因整个 deck（多页 + 大幅居中插图）合进一个 `document.md`，但这是诱因不是根因。

**根因**：CSS flex 链断在 Tiptap 的 `<EditorContent>` 包裹层。`.wysiwyg-editor`（`height:80vh;
display:flex; flex-direction:column; overflow:hidden`）下，滚动样式 `flex:1; overflow-y:auto`
原本加在 `.ProseMirror` 上——但 `.ProseMirror` 不是 `.wysiwyg-editor` 的**直接**子项，中间隔着
`<EditorContent>` 渲染的一个**无样式 div** 包裹层。该包裹层是真正的 flex 直接子项，默认
`flex:0 1 auto` + `display:block`，按内容撑满整篇文档高度；`.ProseMirror` 的 `flex:1` 因父级非
flex 容器而失效、`overflow-y:auto` 因自身高度=内容高度而不触发。包裹层超出 80vh 的部分被
`.wysiwyg-editor` 的 `overflow:hidden` 裁掉且无滚动条 → 只露第一屏。

**浏览器实测证据**（最小复刻 60 段、editor 可视 ~390px）：修前包裹层 7725px、裁掉 7333px、
`scrollable=false`；修后包裹层成为滚动容器、可滚到底、末段可见。

**修复**：给 `<EditorContent className="wysiwyg-editor-content">`，CSS 让**包裹层**承担滚动
（`flex:1; min-height:0; overflow-y:auto`；`min-height:0` 让其能在 flex 容器内收缩到 80vh 以下从而
触发滚动），`.ProseMirror` 改为自然高度 + `min-height:100%`（短文档仍填满可点区域）。

**教训**：给第三方组件（Tiptap/EditorContent 等）做 flex 滚动布局时，确认 `flex`/`overflow`
落在**真正的直接子项**上——组件常在你的容器与内容之间插一层无样式包裹 div，样式加错层级会
静默失效。

## g++/gcc 诊断 LFI：`#import` / `#include_next` 绕过 `#include` 中和（#1d，已修复 2026-06-13）

**现象**：代码诊断的 C/C++ 不安全 include 中和只识别 `#include`（`_C_INCLUDE_RE` /
`_C_INCLUDE_DIRECTIVE_RE` 只匹配 `# include`）。真实 gcc 同样处理 `#import "/x"` 与
`#include_next "/x"`，按路径读取外部文件并把内容当编译错误上下文回显——这两条指令未被中和，
sentinel 复现 `marker in blob == True`，构成与 #1/#1b/#1c 同类的任意文件读取（LFI）。

**根因**：预处理读文件指令不止 `#include` 一种。`#import`（gcc 当作 include-once 的 include）
和 `#include_next` 都会真实打开目标文件；`#include_next` 还会用相对名沿 `-I` 搜索路径解析。
旧正则只认 `#include`，漏掉这两个同类面。

**修复**（`backend/docrestore/processing/code_diagnostics.py`）：把读文件指令集合扩到
`include | include_next | import`：
- 指令本体 `_C_INCLUDE_DIRECTIVE_RE = r"^\s*#\s*(?:include(?:_next)?|import)\b"`，`\b` 词边界使
  `#define IMPORT` / `#includex` 这类非真实指令不误命中。
- 字面量 `_C_INCLUDE_RE` 同步扩展，从这三条指令里提取路径。
- 策略不变：绝对/越级字面量中和；非字面量目标（宏/计算式）一律中和。
- 相对名 `#include_next "h.h"` 不属于 LFI 升级——它只能命中 `-I` 搜索路径，而诊断的 `-I` 全是
  已逐文件中和的影子树，本身受控（非任意文件读）。真正的 LFI 原语是绝对/越级字面量与非字面量。

**验证**：真实 gcc 端到端取证——未修时 `#import "/abs/sentinel"` 与 `#include_next "/abs/sentinel"`
均把 marker 回显进诊断（`does not name a type`）；修后该行变注释、sentinel 不被读取，
`marker not in blob`。新增参数化单测（import/include_next × c/cpp）+ 11 条 `_neutralize` 单元用例，
`TestUnsafeIncludeNeutralization` 40 例全过；临时还原旧正则可复现 4 例失败，证明回归测试非空转。

## API 默认未配 token 即完全放行：fail-closed 自动生成 token + bind 守卫（#35，已修复 2026-06-13）

**现象**：未设 `DOCRESTORE_API_TOKEN` 时鉴权依赖直接 `return` 放行（`auth.py` 旧 :73/:101），仅打
一行 warning；而 `start.sh` 默认 `BACKEND_HOST=0.0.0.0`。两者叠加 → 开箱即「全网未授权可达」，把
同批 RCE（paddle_python）/ 任意 rmtree（output_dir）/ SSRF / PII 等面从「需认证」降级为「未授权可达」。

**根因**：鉴权是 fail-open 默认（无 token = 放行），且默认绑定所有接口。

**为何不照搬 issue 原方案（仅绑 loopback）**：产品方向是「桌面服务 + 手机配对」，手机要从局域网/
远程够到桌面，纯 loopback 会挡掉手机端。故改为**等价或更强**的 fail-closed：永不以未鉴权状态对外可达。

**修复**：
- `auth.py` 新增 `configure_auth_from_env()` 三选一解析（fail-closed）：① 显式 `DOCRESTORE_API_TOKEN`；
  ② `DOCRESTORE_ALLOW_INSECURE=1` 无鉴权逃生口（仅本机调试）；③ 默认**自动生成强随机 device token**
  （`secrets.token_urlsafe(32)`），持久化到用户配置目录（Linux `~/.config/docrestore/`、Windows
  `%APPDATA%\docrestore\`，POSIX 0600），重启复用——即手机配对用的 pairing secret。
- `enforce_bind_safety()` bind 守卫：insecure 无鉴权模式下绑定非环回地址（如 `0.0.0.0`）→
  `RuntimeError` 拒启；环回放行；无法判定（未设 `DOCRESTORE_BIND_HOST`）放行但告警。有 token 时
  任意地址都安全（每请求都校验）。
- `app.py::create_app` 接入 `configure_auth_from_env()` + `enforce_bind_safety()`；新增可选
  `DOCRESTORE_CORS_ORIGINS` allowlist（默认空 = 不挂 CORS，最严格）。
- `start.sh`：默认 `BACKEND_HOST` 0.0.0.0 → 127.0.0.1；启动 uvicorn 前 export `DOCRESTORE_BIND_HOST`
  供守卫校验。

**验证**：`tests/api/test_auth.py` 19 passed（新增 token 三来源解析 + bind 守卫共 11 例）；`create_app()`
三路集成冒烟——显式 token 正常起 / insecure+0.0.0.0 真拒启 / 默认自动生成 43 字符持久 token 并落地。
mypy --strict + ruff + typos 全绿，tests/api 132 passed。

**教训**：安全默认必须 fail-closed。「方便开发」的 fail-open 默认在绑 0.0.0.0 时就是公网洞。面向手机
配对的服务，正确解法不是「锁死 loopback」，而是「默认即有凭据、永不裸奔」，凭据顺带成为配对密钥。

## 请求级覆盖基础设施字段：RCE（paddle_python）+ SSRF（api_base / paddle_server_url）（#32 / #33，已修复 2026-06-13）

**现象**：创建任务的请求体里，OCR/LLM 配置覆盖经 `routes.py` 的 `model_copy(update=req.*.model_dump())`
无差别叠进生效配置，连**基础设施字段**也能被请求覆盖：
- `ocr.paddle_python` → OCR worker 以攻击者指定的任意已存在二进制为 `argv[0]` 启动（`ocr/base.py`
  `create_subprocess_exec`，仅 `Path.exists()` 校验）→ **任意本地命令执行（RCE）**。
- `ocr.paddle_server_url` / `llm.api_base` → OCR 页面图 / LLM 文本（可能含原文、PII）被 POST 到攻击者
  或内网地址 → **SSRF + 数据外泄**，无地址白名单。
- 叠加同批 #35（默认放行鉴权）= 未授权 RCE/SSRF 面。

**根因**：请求级覆盖把「业务可调字段」和「基础设施字段」混在同一个 `model_dump()` 里整体 `model_copy`，
没有区分哪些字段允许外部控制。

**修复**：
- **删字段（schema 层）**：`OCRConfigRequest` 移除 `paddle_python` / `paddle_server_url` /
  `paddle_server_model_name`，pydantic 默认 `extra=ignore` 直接丢弃请求里这些键；前端零引用、无破坏。
- **sink 兜底（allowlist，2026-06-13 自查硬化）**：`routes.py::_resolve_ocr_config` 用
  `_OCR_SAFE_OVERRIDE_ALLOW` **默认拒绝**、只放行登记过的业务字段（`model` / `gpu_id` /
  `exclude_images` / `paddle_pipeline` / `paddle_ocr_timeout`）。初版用 denylist 逐一枚举危险字段，
  自查发现漏了 `paddle_server_host` / `port`（与 `paddle_server_url` 同为 SSRF 出站目标）、`model_path`
  （任意权重加载 → pickle RCE）等同类项——翻成 allowlist 后默认 deny，对 schema 漂移免疫，无需再追问
  「危险字段列全没」。配套测试加 allowlist 与 `OCRConfigRequest` 字段集恒等断言，schema 新增字段忘登记即失败。
- **api_base SSRF 守卫**：新增 `api/url_guard.py::validate_outbound_api_base`——仅 http/https；解析 host
  全部 IP，私网 / 链路本地（含元数据）/ 保留 / 多播 / 未指定一律拒；**环回放行**（本地 LLM 合法目标）；
  可选 `DOCRESTORE_LLM_API_BASE_ALLOWLIST` 白名单逃生口（含内网中转站）。`create_task` 对请求级
  `api_base` 校验（DNS 走 `to_thread`），失败 `400 LLM_API_BASE_REJECTED`、不建任务。

**为何环回放行（偏离 issue #33「连环回一起拦」）**：provider=local 的合法 api_base 就是
`http://localhost:11434/v1`，照搬会误杀本地 LLM。单用户桌面下环回 SSRF 价值极低，元数据
（169.254 链路本地）/ 内网横向仍拦；LAN 上的本地 LLM 走白名单。

**验证**：`tests/api/test_url_guard.py`（21 例：SSRF 各目标拦截 / 环回+公网放行 / scheme /
DNS 解析路径 / 白名单逃生口）+ `tests/api/test_override_security.py`（18 例：schema 丢弃 / sink
allowlist 兜底 / 业务字段仍生效 / 危险字段不在 allowlist / allowlist 与 schema 字段集恒等 /
端点级 400）。两文件 40 passed；tests/api + tests/llm 全量 + mypy --strict + ruff + typos 全绿。

**残留**：DNS rebinding（校验后 TTL 重绑内网）未防，需 connect 级 IP pin，过度工程暂不做。

**教训**：请求级配置覆盖必须显式区分「业务字段（可外控）」与「基础设施字段（只服务端）」，默认 deny
基础设施字段。落地用 **allowlist（默认拒绝、只放行已知安全字段）优于 denylist（逐一枚举危险字段）**——
后者总会漏同类字段（本次就漏了 `paddle_server_host` / `model_path`），且每加一个新字段都得重新自问
「危险吗」。出站地址只要请求级可控就必须过 SSRF 白/黑名单，并把云元数据端点（169.254.169.254）
当一等公民拦截目标。

## output_dir 无边界校验：DELETE 任务 rmtree 任意目录（#34，已修复 2026-06-13）

**现象**：`output_dir` 由创建任务请求体原样带入（`routes.py` → `manager.create_task(output_dir=req.output_dir)`），
删除任务时 `task_manager.py::delete_task` 对其 `shutil.rmtree(output_dir, ignore_errors=True)`。构造
`{"image_dir": "/不存在", "output_dir": "/home/user/work"}`：非法 `image_dir` 让任务**快速进 FAILED**
（终态即可删），随后 `DELETE /tasks/{id}` 删掉整棵 `/home/user/work` → **任意目录递归删除、静默不可逆**。
叠加 #35（默认放行鉴权）= 未授权可达。

**根因**：`output_dir` 是请求级可控的「写/删」路径却无任何边界约束——与 #32/#33 同一类（基础设施级可控量
无白名单）。`rmtree` 是 sink，输入未受信即等于授权删任意目录。

**修复**（两道防线，新增 `pipeline/path_guard.py`）：
- **受信工作根**：`resolve_work_root()` 默认系统临时目录（正是默认输出 `{tempdir}/docrestore_{id}` 的父），
  env `DOCRESTORE_WORK_ROOT` 可拓宽（持久化产物的逃生口，镜像 #33 白名单 env）。
- **准入校验（建任务）**：`routes._resolve_output_dir` 把空串归一为 None（走安全默认），用户显式指定的过
  `validate_output_dir`——`resolve()` 折叠 `..` / 符号链接后必须**严格落在工作根下**（且 ≠ 根本身），
  越界 `400 OUTPUT_DIR_REJECTED` 不建任务。
- **sink 二次校验（删除，TOCTOU 防御）**：`delete_task` rmtree 前再过 `output_dir_within_root`——覆盖建任务
  校验前就存在的历史越界任务、DB 篡改、未来漏接的建任务路径；越界则**拒删、绝不触碰目录**，任务保留在列表里。

**image_dir 不约束（明确判定）**：`image_dir` 是**只读输入**、全链路从不被删除（`collect_referenced_image_dirs`
只用于上传清理时**跳过**被引用目录，是保护而非删除），且合法用法会指向 NAS / 外部只读目录——加同约束反而
误杀正常用法。故只锁 `output_dir`（唯一危险 rmtree sink；`upload_dir` / `stage_dir` 均为服务端 `mkdtemp`
生成，天然受信）。

**验证**：`tests/api/test_output_dir_boundary.py`（17 例：严格子目录放行 / 根本身拒 / 兄弟越界拒 / `..` 逃逸拒 /
符号链接逃逸拒 / 空值拒 / 非抛版判定 / env 工作根来源 / `_resolve_output_dir` 归一与 400 / 端点 400）+
`tests/pipeline/test_task_manager.py::TestDeleteTaskBoundary`（2 例：越界拒删且目录完好 / 自定义根下正常删）。
tests/api + tests/pipeline 全量 **425 passed 17 skipped** + mypy --strict + ruff + typos 全绿。

**教训**：凡「请求级可控 → 落到 rmtree / 写文件 / exec 等 sink」的路径量，都要先锚定**受信根**再做 `resolve()`
后的严格子路径校验，且 sink 处二次校验防 TOCTOU（建时校验只挡正门，符号链接 / DB 篡改靠 sink 兜底）。
只读输入与可删输出要分开对待——别给只读输入也一刀切套删除边界。

## api_key 明文持久化进 SQLite（#37，已修复 2026-06-14）

**现象**：创建任务时 `LLMConfig`（含用户 `api_key`）整体 `model_dump_json()` 落库到 `tasks.llm` 列
（`persistence/database.py`）。API 响应面虽不回显 key，但 **DB 文件 / 备份 / 快照 / 误共享即泄漏长期凭据**。

**根因**：持久化层把配置快照「整体序列化」时未区分「业务配置」与「凭据字段」——`api_key` 是运行期凭据，
不该和 model / timeout 等配置一起落盘。

**修复**（落库排除 + 水合回填 + 存量清洗）：
- **落库排除**：`insert_task` 改 `llm.model_dump_json(exclude={"api_key"})`——key 字段根本不进 DB。全量
  审计确认 `LLMConfig.api_key` 是 `config.py` 里**唯一**凭据字段（OCR / PII / code / ppt 均无），故只锁它。
- **水合回填**：新增 `llm/credentials.py::refill_api_key_from_env`——从 DB 还原（重启水合 / resume）出的
  `LLMConfig` 其 key 为空，仅当为空时从环境 `DOCRESTORE_LLM_API_KEY` 回填（`model_copy` 返回新对象，
  不覆盖显式 key、不原地改）。两个水合点（`load_persisted_tasks` / `get_task_async`）统一过它——resume
  走的 `task.llm` 因此运行期可用。环境变量名常量与 `app.py` 启动回填共用单一真相源。
- **存量清洗**：`initialize` 阶段 `_scrub_persisted_api_keys` 把历史行 `tasks.llm` 内已落的明文 key 从
  JSON 中移除（幂等：已干净行不重写；JSON 损坏行跳过），清除「备份之外、仍在主库里」的存量泄漏面。

**resume 凭据契约**：api_key 不再持久化 → **resume / 重启后必须能从环境拿到 key**。运维须把云端 key 配在
环境（litellm 直读的 `OPENAI_API_KEY` 等，或 `DOCRESTORE_LLM_API_KEY`），别指望「请求体传一次 key」跨重启。

**验证**：`tests/persistence/test_database.py`（落库 raw 串无 key / 启动清洗存量 / scrub 幂等且容错损坏行）+
`tests/llm/test_credentials.py`（4 例：空才回填 / 显式 key 不被覆盖 / 无环境 / 空白环境）+
`test_task_manager.py::test_get_task_async_refills_api_key_from_env`（端到端：落库无 key、水合回填环境 key）。
受影响模块全绿（persistence + llm + pipeline + api，mypy --strict + ruff + typos）。

**教训**：持久化「配置快照」时必须把**凭据字段从序列化中剔除**（`exclude`），凭据走环境变量在运行期回填——
落盘的应是「可分享的配置」，不含「不可分享的密钥」。且要给存量数据补一次性清洗，否则只修了新写入、老泄漏仍在。

## PII 上云前脱敏被多路绕过（#36，已修复 2026-06-14）

**现象**：「上云前脱敏」是项目核心承诺，但 §9 全链路脱敏落地后仍有三条路径让敏感内容以原文送达云端。
标准部署启动级 `PIIConfig.enable=False`、用户在前端按单次任务开 PII（走**请求级** `pii_cfg`），却在多处失效：
- **①（代码模式 header 裸送）**：`_redact_code_pii` 把所有非空 leading-comment header **拼接后原样**
  `detect_pii_entities(combined)`（云端），结构化 PII 的 regex 脱敏在该云端调用**之后**才执行 → 注释里
  `Author: 张三 <a@corp.com>` 的邮箱 / 手机随 `combined` 裸送云端。
- **②（gap-fill / 最终输出读错配置）**：`_fill_one_gap`(:3047) 与 `_finalize_single_doc`(:2177) 判
  `self._config.pii.enable`（启动默认 False）而非请求级 `pii_cfg` → 用户单次开的 PII 在 gap-fill re-OCR
  文本（**绕过 producer 逐页 regex 的全新文本**）与最终输出实体兜底处恒不脱敏。
- **③（代码 prompt 源码片段 / 路径 / 诊断不脱）**：refine/rewrite/repair/audit prompt 把 `file_path` /
  `related_snippets`（含外部 `context_root` 参考片段）/ `path_candidates` / `diagnostics` 拼进云端调用未脱敏；
  其中 repair 诊断在脱敏前算，g++ `summary=output[:1000]` 带 caret 时会回显含 PII 的源码行。

**根因**：请求级 `pii_cfg` 只透传到三模式分支入口，未贯穿到深层 helper（②回落启动级配置）；脱敏与「拼 prompt /
送检」的**先后顺序**在代码模式被写反（①先送后脱）；代码 prompt 的非 `merged_text` 派生字段（路径 / 外部片段 /
诊断）从未纳入脱敏面（③）。

**修复**：
- **①**：拼 `combined` **前**先对每个 header `redact_regex_only`，再 `detect_pii_entities`；lexicon 仍基于
  （已结构化脱敏的）注释，人名照常检测。
- **②**：`pii_cfg` 一路透传 `_stream_process → _finalize_single_doc → _maybe_fill_gaps → _fill_gaps →
  _fill_one_gap`，:2177 / :3047 改用请求级 `pii_cfg`，禁止回落 `self._config.pii`。
- **③**：请求级 `pii_cfg` 建 `redact_regex_only` 函数（`_make_regex_redactor`）下传 `CodeLLMRefiner` /
  `DiagnosticCodeRepairer` / `CodeConsistencyAuditor`；在 `json.dumps` **之前**对 `file_path` /
  `related_snippets` / `path_candidates` / `diagnostics` 按字段脱敏——先脱后序列化，`json.dumps` 对占位符里
  任何引号（用户可自定义 placeholder / code）正确转义，**绝不破坏 JSON**。

**PPT 模式经核查为干净**：`_ppt_pipeline` 自 §9 起即正确透传 `pii_cfg`、producer 逐页 regex、每页精修前
`redact_snippet` + 组装兜底 + fail-closed，无 `self._config.pii` 误读，本次不改。

**验证**：`test_request_level_pii_redacts_reocr_text`（②gap-fill）/ `test_finalize_output_uses_request_pii_when_startup_off`
（②最终输出，含「回退 bug 必失败」反验证）/ `test_header_structured_pii_masked_before_detect`（①送检入参已掩码）/
`test_redact_masks_prompt_fields` + `test_file_path_redacted_in_refine_prompt` + 对照 `test_no_redact_leaves_prompt_fields_raw`
（③prompt 字段脱敏且产物合法 JSON）。PII + 代码模式相关 140 passed，全量 1296 passed（3 个 DeepSeek 失败为本机
未配 OCR python 路径的既有环境问题，与本次无关），mypy --strict + ruff + typos 全绿。

**教训**：「上云前脱敏」要把**请求级配置贯穿到每一个云端 sink**（深层 helper 不得回落启动默认）、**脱敏必须在
拼 prompt / 送检之前**（顺序写反等于没脱）、并覆盖**所有**进 prompt 的字段（不止正文，路径 / 外部片段 / 诊断同样
外发）。结构化字段脱敏放在 `json.dumps` 之前，序列化层天然兜住占位符转义。

## 请求级 api_key 任务重启后不可 resume（#64，已知限制 2026-06-16）

**现象**：用请求体 `llm.api_key` 建的任务，服务重启后 resume / retry 时云端精修缺 key，
每段 401 被 `except` 回退原文，**静默产出未精修结果**且无错误暴露。

**根因**：#37 把 `api_key` 排除出 DB（明文持久化是长期凭据泄漏面），DB 水合只能从环境变量
`DOCRESTORE_LLM_API_KEY` 回填（`llm/credentials.refill_api_key_from_env`）。若原 key 来自请求体
而非环境，重启后无处可回填 → 水合出的 `LLMConfig.api_key` 为空。

**为何不在 resume 入口硬拦**：`provider="cloud"` + 空 key 并非总是错误——指向**无鉴权本地/中转
代理**（环回 api_base）的合法用法同样空 key，一刀切 400 会误伤既有可用任务（回归）。无法在
resume 时区分「请求级 key 丢失」与「本就无需 key」，故按 issue #64 验收的「**或文档化**」分支处理。

**规避**：需 resume 的云端精修任务，把 key 配进环境变量 `DOCRESTORE_LLM_API_KEY`（重启后水合自动
回填），而非仅放请求体；或重新建任务并在请求体重新提供 key。本地 LLM 用 `provider="local"`，不受影响。

## Pillow 首次 RGB→PDF 保存 KeyError 'JPEG'（Epic A 渲染，2026-06-18）

**现象**：在全新 Python 进程里第一步就 `Image.new("RGB", ...).save(path, save_all=True,
append_images=[...])` 存多页 PDF，报 `KeyError: 'JPEG'`（`PdfImagePlugin._write_image`
里 `Image.SAVE["JPEG"]` 不存在）；但 `features.check("jpg")` 明明为 True。

**根因**：Pillow 插件**懒加载**。`Image.save()` 只触发 `preinit()`，而 PDF 保存对 RGB 页
用 JPEG(DCTDecode) 编码，需要完整 `Image.init()` 注册的 `Image.SAVE["JPEG"]` 处理器。
若进程内此前没有任何操作触发全量 init（如 `features.check`、open 一张 jpg），`Image.SAVE`
里就没有 JPEG，保存即 KeyError。与 libjpeg 是否安装**无关**（本机 jpg 支持正常）。

**规避**：造 PDF（fixture / 任何 RGB→PDF）前显式 `Image.init()`。已在
`tests/pipeline/render/test_pdf.py::_make_pdf` 落地。生产渲染路径用 pypdfium2 读 PDF、
不走 Pillow 存 PDF，不受影响。

## 开 PII 误伤英文专有名词 / 图片标识符（已修复 2026-06-18）

现象：
- 开启 PII（人名/机构名脱敏）后，输出大量误伤：`FGRFP→[机构名]FP`、图片 src `..._501_94_after_1.jpg→..._[机构名]_1.jpg`、LaTeX `\mu→[人名]`、HTML `break-word;'>kcat→break-word[人名]`、`Metallosphaera sedula→[人名] sedula`。
- 关 PII 输出正常——问题专属实体（人名/机构名）替换路径，结构化 regex 脱敏无辜。

根因（两层相乘）：
- 检测层：`pipeline` 把含图片引用/HTML/LaTeX/代码的**完整 markdown** 喂给通用 spaCy NER，把 `xxx.jpg`/`;'>kcat`/`\mu`/整句误检为人名/机构名。
- 替换层：`redactor._replace_entities` 用无词边界、无结构豁免的 `str.replace`，一条坏词全文穿透（词内子串 `FGR`→`FGRFP`、图片 src、HTML 属性、LaTeX）。

处理策略（详见 [pii-entity-overredaction-fix.md](backend/pii-entity-overredaction-fix.md)）：
- A 替换层结构感知：新增 `privacy/markup.py` 结构跨度单一真相源；实体替换只在自由文本段、ASCII 实体加词边界。
- B 检测层：检测前 `mask_structure` 抹掉结构再喂 NER；`_looks_like_name` 净化丢弃文件名/markup 碎片/数字串/整句。
- 残留：通用 NER 对"长得像名字的领域词"（物种名/期刊名）在正文里的误检属固有精度上限（设计 N1），结构已零损坏；如仍嫌噪可后续加英文停用表（设计 D1）。

## 导出 pptx 漏出 HTML 标记 + docx 丢表格/图片（Epic D Phase-2a，已修复 2026-06-23）

现象（用户报告）：
- **pptx**：slide 上出现一长串 `<table border=1 style='...'>...` 与 `<div style="text-align:center;">a)</div>`
  原始 HTML 标记当字面文本；表格没渲染成表格。
- **docx**：表格与图片**全部丢失**，只剩纯文本；同一份 `document.md` 导出 **PDF 正常**。

根因（同一类，"原始 HTML 不被目标 writer 认"）：
- **docx**：`document.md` 的表是 HTML `<table>`、配图常含 HTML `<img>`。单遍 `pandoc -f gfm -t docx`
  把原始 HTML 当 `RawBlock html` 保留，而 **docx writer 直接丢弃原始 HTML** → 表格 + HTML 图片消失
  （仅 `![]()` 图片侥幸保留）。PDF 正常是因为 PDF 链路目标是 HTML（`-t html5`），原始 HTML 原样透传、
  weasyprint 原生渲染。测试此前用 GFM 管道表（pandoc 原生认）测不出该回归。
- **pptx**：自拼页时把整行文本直接塞进文本框，`<table>`/`<div>` 标记当普通文本一起塞。

修复：
- **docx 改两遍 HTML 中转**：`pandoc gfm+tex_math_dollars -t html5 --mathml`（原始 `<table>/<img>` 内联、
  `$..$`→MathML）→ `pandoc -f html -t docx`（HTML reader 把 `<table>/<img>` 转原生、MathML→OMML）。
  **`--mathml` 而非 `--mathjax`** 是关键：`--mathjax` 产 `\(..\)`，HTML reader 不再解析回数学（OMML 丢失）。
- **pptx 改按块解析**：一页拆成有序块（正文/表格/图片），`<table>` 复用公共解析层 `html_table.py`
  渲染成**原生 pptx 表格**（含合并区），散文剥 HTML 标签只留文本，竖向堆叠。
- 测试据实改用 **HTML `<table>` + HTML `<img>`**（真实 `document.md` 格式）锁回归：docx 断言
  `document.tables` 有派生单元格 + `inline_shapes >= 2`；pptx 断言原生表格有派生单元格 + 文本框无 `<table`/`<div` 漏出。
- 详见 [export-mode.md](export-mode.md) §6（docx 两遍）/§9.2（pptx 按块）。

## PPT 版面定位 sidecar 图片引用丢失 `_after` 前缀（E2E 实测发现，已修复 2026-06-24）

**现象**：真机 E2E（活 VL OCR 跑 3 张 PPT slide）后，`.ppt_layout.json` 的图片区域
`image_ref` 形如 `images/{stem}_4.jpg`，但盘上真实裁图是 `images/{stem}_after_4.jpg`，
导出器 `_resolve_image` 解析不到 → positioned pptx 图片区域空缺。

**根因**：PPT 模式 `rectify=True` 时 OCR 跑在矫正后图 `{stem}_after.jpg` 上，`PageOCR.output_dir`
= `{stem}_after_OCR`，`Renderer` 按它命名裁图为 `images/{stem}_after_N.jpg`；但 producer
（`pipeline.py:1964`）把 `page.image_path` **改回了原图**（stem 无 `_after`），而
`_write_ppt_layout_sidecar` 误用 `page.image_path.stem` 算最终引用 → 少了 `_after`。
`document.md` 的图引用正常，因 `Renderer` 自己用 `page.output_dir.name` 算。

**修复**：sidecar 改用与 `Renderer`/`rewrite_image_refs_to_ocr_dir` **同源**的命名 stem——
`page.output_dir.name` 去掉 `_OCR` 后缀（兼容 `{stem}_after_OCR` / `{stem}_cropped_OCR` /
`{stem}_OCR`），fallback `page.image_path.stem`。回归单测 `test_ppt_layout_sidecar.py::
test_sidecar_image_ref_uses_ocr_dir_stem_after_rectify` 锁定。

**附带**：`_stream_pipeline` 原 `contextlib.suppress(Exception)` 把 OCR 生产者真实异常吞掉、
被消费者「未产出任何页」掩盖，排障困难——改为抽 `_cancel_producer_log_real` 取消后 `warning`
记录生产者真异常（不改变抛出语义），便于日后定位 OCR 失败根因。

**教训**：跨组件「同一资源的命名」必须单一真相源（这里是 OCR 目录名），不能各算各的；纯函数单测
用 `{stem}_OCR` 凑巧 stem 相同测不出，**真机 E2E（矫正后 stem 含 `_after`）才暴露**。

## PPT 区域取色：合成测试通过但真机几乎全弃权（量化过细 + frac_bg 阈值太严）

**现象（2026-06-24）**：positioned-pptx 区域颜色采样（§11）合成像素图单测全过（黑字白底 / 暗色模式 /
彩字取色全对、弃权用例全 None），但拿真机 3 slide 的真 VL 区域跑，**13 个文字区域里 12 个弃权**、
只剩 1 个采到色——功能在真实幻灯片上几乎不生效。

**根因**：弃权守卫 `frac_bg`（最大量化桶占样本比例）门槛 0.35 把真机文字区域全卡掉。诊断打印各区域
守卫值发现：contrast / Δlum 都很高（195~252 / 108~144，**明显是文字区**，前景背景分离良好），唯独
`frac_bg` 普遍 0.13~0.39（中位 0.25）卡在 0.35 下。深层原因是**真机屏摄的 JPEG 压缩 + 抗锯齿
把单一底色散布到多个相邻量化桶**：背景虽占 70%+ 像素，但 16 级量化（`>>4`，桶宽 16）下被噪声 ±8
劈成 2×2×2 个邻桶，最大单桶只兜住 ~25%。合成图底色**完全均匀**（单桶 ~85%）所以测不出。

**修复**：量化粒度 16 级（`>>4`）→ **8 级（`>>5`，桶宽 32）**，噪声 ±8 多落同桶；frac_bg 阈值
0.35 → **0.15**（真机文字区 8 级下 frac_bg 中位升到 0.35、最小 0.17）。代表色仍取**桶内真实像素均值**，
粗量化不损色精度（只影响分桶聚合）。改后真机 12/13 文字区采到合理色（表格因 contrast<60 正确弃权），
暗色蓝底 banner 正确取浅前景 / 深背景；合成弃权用例（纯色块 / 低对比 / 双色块 / 噪声）仍全 None。

**教训**：**纯合成测试无法替代真机调参**——合成图的「完美均匀色块」掩盖了真实屏摄的 JPEG / 抗锯齿
色散，阈值在合成上随便定都过、到真机才暴露过严。涉及「从真实退化图像采统计量」的阈值，**必须拿真机
数据标定**（复用 Phase-2b 残留 `.rectified/` + 真 sidecar bbox，免 GPU 即可标定）。

## PPT 区域取色：相机白平衡偏色被原样搬进 pptx（白底拍成蓝→填成蓝背景）

**现象（2026-06-24，用户报）**：positioned-pptx 颜色采样把**相机拍摄的色差**原样还原——拍照白平衡
偏蓝，本该白的背景被采成淡蓝（如 `(135,167,230)`），渲染就把白底文本框填成蓝色，导出 slide 背景发蓝。

**根因**：采样如实反映像素，但像素本身带相机偏色。**过度忠实**地复刻了拍摄缺陷而非原始幻灯片。

**修复**：加全局**白平衡校正**。`estimate_white_balance(arr)` 整页估每通道增益 `gain_c=255/白点_c`
（白点=每通道 95 百分位=背景白），把背景白映射回真白；增益限幅 4.0、白点最亮通道 <100 则不校正
（暗色主题不强制白化）。增益**只施于最终输出色**（守卫仍用未校正色，真机标定阈值不动）。配套把渲染
「视为白底不填」规则从「各通道≥240」改成 **`_is_effectively_white`：最暗通道≥200 且通道极差≤30**
（浅且近中性）——因校正后白底落浅中性灰（`(210,224,229)` 这类 <240），饱和度判别既吸残留偏色、
又不误伤真彩浅色（浅蓝 `(200,220,255)` 极差 55 仍填）。真机重渲染：白底还原白、仅真彩 banner 填色。

**教训**：「还原原文」≠「复刻照片」——采样统计量会把**拍摄链路的系统性偏差**（白平衡 / 曝光 / 色偏）
一并搬进结果。凡「从照片采颜色 / 亮度」的特征，须在采样前 / 后做一次**拍摄畸变归一化**（白平衡是最常见的一项）。

## Epic E：源图 `data-page` 锚点从 `<img>` 移到外层 wrapper，破坏按 data-page 取 img 的测试

现象（Epic E E3）：为承载 bbox 高亮 overlay，`SourceImageList` 把每张 `<img>` 包进
`.source-image-cell`（`position:relative`），并把 `data-page` 从 `<img>` **移到外层 cell div**。
`CodeViewer.test.tsx` 原断言 `.code-source-images-list [data-page="X"]` 取到的元素有 `alt` 属性
（旧结构 data-page 在 img 上），结构变更后该选择器命中的是 cell div（无 `alt`）→ `getAttribute("alt")` 返回 null 失败。
per-file lint/tsc hook 看不出（跨文件、运行期 DOM 断言），全量 `npx vitest run` 才暴露。

处理策略：
- 结构变更后，**按 data-page 取的是锚点容器，不再是图片本身**；要图片标识改查内层 `[data-page="X"] img` 的 `alt`。
- scroll-sync 仍按 `[data-page]` 定位：cell 紧裹 img、垂直 offsetTop 等价，锚点数量不变，**零回归**（勿在 cell 与 img 上同时打 data-page，否则双锚点扰乱连续映射）。
- **教训**：改动共享底层组件（`SourceImageList` 被文档+代码模式共用）的 DOM 结构后，必须跑**全量**前端测试，不能只信 per-file hook。

## 前端 `unicorn/no-useless-undefined`：箭头/回调里禁 `return undefined`，函数声明里放行

现象（Epic E E3）：在 `useMemo(() => { if (x) return undefined; ... })` 这类**箭头回调**里写
`return undefined` 被 `unicorn/no-useless-undefined` 判错；但同样的 `return undefined` 写在**具名函数声明**
（如 `function computeBlockHighlight(): T | undefined { if (...) return undefined; }`）里却放行。
直接把 `undefined` 当实参传（`fn(LAYOUT, undefined)`）也被该规则判为「useless」。

处理策略：
- 需要早返回 `undefined` 的逻辑，抽成**具名函数声明**（顺带更可单测）；箭头/memo 只调用它。
- 传 `undefined` 实参时改用具名变量（`const noCursor: T | undefined = undefined; fn(a, noCursor)`），
  或对可选参数直接省略（`fn(a)`）。
- 此约定与 codebase 既有写法一致（既有代码用三元 `cond ? undefined : val` 而非 `return undefined` 语句）。

## 前端 lint：`Element.textContent` 非空 + `unicorn/no-null` 拿真实 null 的写法

现象（Epic E E4，#88）：
- `block.textContent ?? ""` 被 `@typescript-eslint/no-unnecessary-condition` 判错——本项目 DOM 类型里
  `Element.textContent` 推断为 `string`（非 `string | null`），`?? ""` 左侧不可能为空 → 多余。
  对 `Element`（非裸 `Node`）直接 `block.textContent.trim()` 即可（Element 的 textContent 运行期恒为字符串）。
- 单测里要传「运行期 null」给入参（如模拟 `e.target` 为 null）时，**禁止写 `null` 字面量**
  （`unicorn/no-null`），也别用 `x ?? null` 把 `undefined` 转 null（同样判错）。

处理策略：
- 取真实 null 用 `container.querySelector(".does-not-exist")`（落空返回真 `null`，类型 `Element | null`），
  既覆盖 null 运行期路径，又不写字面量。
- 纯函数入参若既可能 null 又可能 undefined，签名直接放宽到 `Element | null | undefined`，
  调用侧（`querySelector` 返回 `Element|null`、`at(-1)` 返回 `Element|undefined`）都能直传，免去 `?? null` 转换。

## 光标高亮在 PPT 任务上不亮：是「文档模式特性 + PPT 坐标系」而非缺陷（#90）

现象：用户在 PPT 模式任务里把光标放文档上，右侧原图不出现 bbox 高亮框。

根因（排查链）：
- 该任务 `ppt.enable=true, rectify=true`（DB `tasks.ppt` 字段、`.rectified/` 目录、`*_after_OCR` 可佐证）。
- E3/E4 高亮是**纯文档模式**：只读 `.layout.json`（`_write_doc_layout_sidecar` 仅在 `_finalize_single_doc`
  调用），PPT 模式只落 `.ppt_layout.json` → `/layout` 404 → 前端 fail-safe 不高亮。
- 即便有数据，PPT bbox 在**矫正图 `_after.jpg`** 坐标系，源图栏却显**原图**（透视矫正后长宽比已变）→ 不对齐。

处理：#90 让 `/layout` 在 `.layout.json` 缺失时回退读 `.ppt_layout.json`（含 filename/image_size/regions），
置 `rectified=true`；前端据此把源图栏改显矫正图 `_after.jpg`（坐标系对齐）。**现有 PPT 任务零重跑即可高亮**。

排查教训：报「功能不工作」先确认**被测任务的模式/配置**（查 DB `tasks` 行 + output_dir 里落了哪种
sidecar），别假设是代码 bbox bug——很多「不工作」是数据/模式不匹配（旧任务无 sidecar、PPT vs 文档）。

## 前端 zod `.default()` 让 `z.infer` 类型字段变**必填**（输出类型）

现象（#90）：`LayoutPayloadSchema` 加 `rectified: z.boolean().default(false)` 后，`type LayoutPayload =
z.infer<...>` 的 `rectified` 是**必填** `boolean`（zod 的 output 类型含默认值后的字段），导致测试里
手写的 `LayoutPayload` 字面量（缺 `rectified`）`tsc -b` 报 TS2741。per-file hook 的逐文件 tsc 看不出
（错在**别的**测试文件引用该类型），全量 `npm run typecheck`(`tsc -b`) 才暴露。

处理：给所有手写该类型的测试 fixture 补上新字段（`rectified: false`）。生产代码不受影响——它走
`handleResponse`→zod 解析，`.default()` 在解析期补值，无需手写。

## 光标高亮框「非常不准」：OCR 前预处理坐标系不匹配（通用，#90 只解了 PPT）

现象：文档模式光标高亮框水平方向严重错位（左偏 + 拉宽），**没开 LLM 精修也错**（排除匹配问题）。

根因（`pipeline.py:2047`）：OCR 前任一预处理——PPT 透视矫正 / content_crop 正文裁剪（**默认开**）/
手动裁剪——后，`ocr_input` 是处理图，OCR 出的 `image_size`+`layout_regions.bbox` 在**处理图坐标系**，
但 `page.image_path` 被改回原图（marker/源图按原名匹配）。Epic E sidecar 落的就是处理图坐标，而
源图栏显示**原图** → bbox 与显示图坐标系不符。实测 content_crop：裁剪图 1418 宽、原图 2467 宽，
标题框画在 3%–81% 而实际 ~23%–68%，左偏 ~20% + 拉宽 1.74×。

排查关键证据：浏览器注入 device token（`~/.config/docrestore/device_token`，localStorage key
`docrestore_api_token`）打开真实任务，hover 块后 `evaluate` 抓 overlay 的 `style.left/width` +
`img.naturalWidth` 一比就实锤（naturalWidth=原图 2467 vs image_size=裁剪 1418）。

处理（§15）：把 #90 的「显处理图」机制**通用化**——`rectified`→`processed`、`rectified-image`→
`processed-image`（逐 variant 探 `.rectified/_after` + `.content_crop/_crop`）。`processed` 按
**探处理图目录是否有文件**统一判定；源图栏对处理页改显处理图、`onError` 回退原图（未处理页 bbox
本在原图系，逐页混合自洽）。**现有任务零重跑**（裁剪图/矫正图已在盘）。

教训：**任何「OCR 在变换后的图上跑、却把坐标当原图用」都会错位**。新增预处理（去畸变/裁剪/旋转）
时，要么把 bbox 变换回原图坐标，要么前端显示处理图——二选一，别让两个坐标系混着用。

## curl localhost 返回 Privoxy 502：环境 http_proxy 拦截本地请求

现象：`curl http://127.0.0.1:8000/...` 返回 `502 ... (Privoxy@localhost)`，但服务其实正常。
根因：环境设了 `http_proxy`/`https_proxy` 指向 Privoxy，curl 默认走代理，代理转发不到本地端口。
处理：本地 curl 加 `--noproxy '*'`（或 `NO_PROXY=127.0.0.1`）。Playwright/浏览器访问 localhost
不受影响（自有网络栈）。

## 错误处理：掩盖问题的兜底（landmine）批量整改（2026-06-29 审计）

现象 / 背景：
- 全量审计 343 个 `except` + 32 个 `suppress` + 55 个前端 `catch`，找出"掩盖问题的兜底"。
- 总评：项目错误处理底子不错（多数有日志 / 用 `PipelineResult.error`/`flags`/`quality_report`
  暴露失败 / PII 默认 fail-closed / 大量窄类型 except），地雷密度低。
- **唯一成体系反模式 = 输出/渲染链路的"静默数据丢失"**：产出"看似完整其实缺东西"的产物，
  只有服务端日志（或无日志），不进 `result.error`，用户无感。

根因（4 个真地雷，已修）：
- `output/renderer.py` `_copy_image`：源图缺失仍重写引用 → `document.md` 死图引用，零日志。
- `pipeline/render/pdf.py`：单页渲染 `except Exception` 吞编程 bug；缺页被 sentinel 永久缓存
  （`_read_sentinel` 只校 sha 不校 `rendered==expected_pages`）；调用方丢弃返回值缺页不进 error。
- `llm/base.py` `refine`/`final_refine`：`content or ""` 把模型空响应当成"成功精修成空文档"，
  小段还写进缓存污染 resume。
- `persistence/database.py` `_migrate_add_column`：`suppress(Exception)` 裹裸 ALTER，吞磁盘满/锁/语法错。

处理策略（**新代码必须遵守的四条**）：
1. **静默处补日志**：宽/窄 except 后若 `pass`/`continue`/`return 默认值`，至少 `logger.warning`
   （或 debug，视严重度），让失败可见。完全静默 = 缺陷，哪怕是窄类型。
2. **收窄 except 类型**：只兜可预期的运行/IO 异常（`OSError`/`ValueError`/库自有异常），别用宽
   `except Exception` 顺手吞掉 `KeyError`/`AttributeError`/`TypeError` 等编程 bug——它们应崩出来。
   构造对象（pydantic 等）尽量移出 try，避免校验 bug 被当成"读取失败"。
3. **丢弃的数据要透出**：被跳过/丢失的项收集进 `skipped`/`missing` 计数，透到 `result`/日志
   （范式见 `output/code_renderer.py` 的 `logger + skipped[]`）；产物层截断要在产物里留可见标记
   （如 xlsx 截断行）。
4. **区分"预期不适用"与"真实失败"**：前端 `catch` 用 `isNotFoundError`（`ApiError.httpStatus===404`）
   把 404（任务未完成/非代码模式）与 500/网络/解析错误分开——404 静默、其它 `console.error`，
   不能把请求失败伪装成"正常空态"或"无限处理中"。
5. **取消必须透传**：`suppress`/`except` 不要包含 `asyncio.CancelledError`；`gather(return_exceptions=True)`
   的结果若是 `CancelledError` 要 `raise` 而非当普通失败回退（项目约定"CancelledError 一路传播"）。
6. **安全边界 fail-closed 优先 / 至少可见**：出云 PII 闸口在云端 provider 无策略时记 warning
   （生产路径恒装策略，命中即接线 bug）；删除/清理失败不报成功（DB 删除失败返回错误，避免重启幽灵任务）；
   引用集合收集失败上抛让清理跳过本轮（fail-safe），不返回残缺集合误删在用目录。

提交：分支 `bugfix/error-handling-hardening`，A/B/C/D 四组 commit；门禁全绿。

遗留（建 issue 跟踪，未在本批改代码逻辑）：
- **#95** `privacy/redactor.py` `_looks_like_name` 把含撇号/连字符的西文人名（O'Brien 等）整条丢弃，
  §A `split_protected` 落地后该过滤比结构所需更宽 → 一类真实人名漏脱出云。本批仅加"丢弃计数"
  可观测；收窄标点集需配 PII 召回测试，单独排期。
- **#96** `ocr/engine_manager.py` VL 缺 server python 退本地、`pipeline/render/pdf.py` 缺页：已记 warning
  但未透到任务 `result.error`/前端；如需用户侧可见需引擎状态/任务级透传，单独排期。

## PPT 模式不应自动裁剪（§14.2 已回退）

现象：
- 用户反馈"PPT 模式任务（如 121528c1）自动裁掉了几张幻灯图，记得 PPT 没有前置裁剪"。

根因：
- 2026-06-25 §14.2 曾有意把文档模式的正文自动裁剪（content_crop）扩到 PPT（透视矫正后串联裁剪），
  PPT 从 `skip_content_crop` 移出。屏摄幻灯无固定正文列，矫正后再自动裁会误伤图文版式。

处理策略（2026-06-29 回退）：
- PPT 重新纳入 `skip_content_crop`（`skip = code_cfg.enable or ppt_cfg.enable or is_pdf_rendered_dir`），
  PPT 只做透视矫正、**不自动裁剪**；需要裁剪走手动框（任务级 `crop_boxes`，独占、模式无关）。
- `_processed_source_variants` 移除链末 `_after_crop` 变体（PPT 不再产）。
- 回归测试 `tests/pipeline/test_ppt_content_crop_skip.py` 锁定：PPT 不产 `.content_crop`，文档模式同图作对照。
- 历史任务（如 121528c1）已落盘的 `.content_crop/*_after_crop.*` 是回退前产物，重跑该任务即不再裁剪。
