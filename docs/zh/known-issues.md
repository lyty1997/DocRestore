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

## 代码模式不尊重 block_cloud_on_detect_failure（#25 = #10 残留，已修复 2026-06-04）

现象：
- #10 只让文档 / PPT 模式 fail-closed；自查发现**代码模式**仍漏。
- `_redact_code_headers` 实体检测失败时只 log、退化成"仅 regex + 自定义词"（header 人名/机构名未脱），且**不向上游返回失败信号**；随后 `_code_pipeline` 把 `src.merged_text` 经 `code_refine` / `code_repair` / `code_audit` 送云端，无 fail-closed 闸门 → 开 PII + 检测失败 + flag 真时仍外发含真实姓名的代码头。

处理策略：
- `_redact_code_headers` 改返回 `block_cloud: bool`（实体检测**已尝试且失败** + `block_cloud_on_detect_failure` 为真）。
- `_code_pipeline` 在 `refine_on` 闸门加 `and not pii_block_cloud`，跳过整段 refine / repair / audit 云端循环（退化为不精修的本地输出）。
- 回归：`tests/pipeline/test_code_pii_header.py::TestRedactCodeHeadersFailClosed`（检测抛错→True / flag 关→False / 检测成功→False / 无 refiner→False）。
