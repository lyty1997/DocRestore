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

暂缓（低优先级，择期清理）：关精修时仍报“精修第 X 页”进度文案、`progress.pptDone` 死 i18n 键、`_ocr_config_for_ppt_mode`/`_ocr_config_for_code_mode` 克隆可合一为参数化 helper、retry/resume 无 PPT 产物兜底（对称于 `_retry_code_config`）、关精修时 `_ppt_pipeline` 仍建空 `.llm_cache/` 目录。
