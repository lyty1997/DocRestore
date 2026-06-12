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
| 人名/机构名 | LLM `detect_pii_entities` | ⚠️ 部分：**早窗口已修**（词表就绪前不送云端）；仅剩检测调用本身把文本发云端（LLM 检测固有） |

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
1. **人名/机构名检测调用曝光**：实体检测必须把文本给 LLM 才能认（用云端 provider 时即外发一次）。
   早窗口段精修曝光已修；彻底规避检测曝光需本地 NER / 本地检测 provider。用户已认可"上云前完全
   脱敏不现实，能拦多少拦多少"。
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
