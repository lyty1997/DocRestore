# 开发进度

## 2026-05-14 22:59:47 CST - AGE-63 路径置信度参与代码文件分组

完成内容：
- `code_file_grouping` 的 canonical filename / dir 选择改为优先使用路径置信度总和，再看频次和长度，避免低置信 OCR 变体覆盖高置信路径候选。
- 多 segment 合并时，如果存在低置信路径参与，会写入 `code.grouping.low_confidence_path_merged` 风险 flag。
- 保持 `group_into_files(PageColumn)` 入口兼容，内部开始消费 AGE-62 的 `path_confidence`，为后续更彻底的 `CodeSegment` 分组重写铺路。
- 补充分组测试，覆盖低置信 filename 变体、兼容目录合并时的高置信目录选择和低置信合并 flag。

验证：
- `python -m pytest tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，54 passed, 7 skipped。
- `ruff check backend/docrestore/processing/ide_meta_extract.py backend/docrestore/processing/code_file_grouping.py backend/docrestore/output/code_renderer.py tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py`：通过。

遗留问题：
- 本次仍保留 PageColumn 输入，尚未完全改成 `group_segments_into_files()`；同页多 column 归同路径的强约束仍沿用既有 `_enforce_one_page_one_file()`。

## 2026-05-14 22:53:13 CST - AGE-62 代码来源置信度建模

完成内容：
- 新增 `PathCandidate`，`IDEMeta` 保留 breadcrumb、tab、peer 等路径候选、原始文本、候选来源和置信度。
- `IDEMeta` 新增 `path_confidence`，保留旧的 `filename` / `path` / `language` 字段，确保现有分组逻辑兼容。
- 新增 `CodeSegment` 与 `segment_from_page_column()`，把每张图每个 column 转成可审计的最小来源单元。
- `files-index.json` 新增 `path_confidence` 与 `source_segments`，每个 segment 包含来源页、column、bbox、行号范围、选中路径、路径候选和 flags。
- 补充测试覆盖 breadcrumb/tab 候选、tab fallback、peer 补全、segment provenance 和 renderer 索引输出。

验证：
- `python -m pytest tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py -q`：通过，45 passed, 7 skipped。
- `python -m pytest tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，7 passed。
- `ruff check backend/docrestore/processing/ide_meta_extract.py backend/docrestore/processing/code_file_grouping.py backend/docrestore/output/code_renderer.py tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py`：通过。

遗留问题：
- AGE-62 只建立 provenance 和置信度数据面；AGE-63 才会把分组逻辑切到 `CodeSegment` / `PathCandidate`。

## 2026-05-14 22:44:33 CST - AGE-61 代码模式质量报告闭环

完成内容：
- `files-index.json` 新增代码模式质量摘要：组合后的 `flags`、`source_file_flags`、`source_column_flags`、来源页/column 计数和轻量 `quality` 字段。
- 新增 `detect_code_mode_quality()`，把 `code.refine.truncated`、大 gap 折叠、缺失行号、未知文件名、meta fallback、unpaired code、路径安全降级等代码模式风险写入 `.quality_report.json`。
- `merged_pages` 仅作为规模信息保留，不单独生成质量 issue，避免把大文件多页合并误判为失败。
- 代码模式 pipeline 在渲染后接入质量检测，新任务会产出非空代码质量报告。
- 补充 renderer 和 quality report 单元测试，覆盖 flags 聚合、旧索引兼容、代码模式 issue 生成和路径安全降级。

验证：
- `python -m pytest tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_zip_code_mode.py tests/api/test_code_mode_e2e.py -q`：通过，35 passed, 1 skipped。
- `ruff check backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/quality_report.py backend/docrestore/pipeline/pipeline.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- AGE-61 只建立质量可观测性闭环，不改变分组、OCR 或 LLM 修复策略；后续继续 AGE-62。

## 2026-05-14 18:37:20 CST - AGE-58 实现路径拆分为子 issue

完成内容：
- 在 Linear 中将 AGE-58 拆成 9 个 child issue：AGE-61 到 AGE-69，覆盖质量报告、`CodeSegment` / `PathCandidate`、分组重写、UI 噪声与确定性清理、多语言诊断、小窗口 LLM 修复、全文件一致性 pass、二次裁剪 OCR 和可选代码库上下文。
- 为子 issue 添加父子关联到 AGE-58，并设置关键依赖关系：AGE-62 依赖 AGE-61，AGE-63/AGE-64 依赖 AGE-62，AGE-65 依赖 AGE-61/AGE-64，AGE-66 依赖 AGE-62/AGE-65，AGE-67 依赖 AGE-66，AGE-68 依赖 AGE-62/AGE-64，AGE-69 依赖 AGE-62/AGE-66。
- 在 AGE-58 留下拆分总览评论，说明主线最短闭环和增强线执行顺序。
- 更新 `docs/zh/backend/age-58-code-mode-quality-plan.md`，新增 Linear 子 issue 拆分表。

验证：
- Linear 返回的子 issue 编号与父子关联已确认。

遗留问题：
- 尚未开始实现；建议从 AGE-61 开始建立质量报告闭环。

## 2026-05-14 18:29:52 CST - AGE-58 小段后全文件一致性修复设计

完成内容：
- 补充 AGE-58 修复方案 9.1 节，明确小段修复后可以做全文件 pass，但定义为“全文件一致性审计 + 受限 patch”，不是整文件重写。
- 设计 prompt 组织原则：区分 `editable ranges` 与 `read-only excerpts`，全局上下文可包含 file outline、symbol table、imports/includes、诊断、前序局部修复摘要、重复 OCR 混淆和 unresolved 项。
- 明确大文件不发送完整代码，改用 outline、diagnostics、numbered excerpts、suspicious ranges 和 previous repairs 组织上下文。
- 要求全文件 pass 输出结构化 JSON patch，绑定原始行号范围；未列入 editable range 的问题只能返回候选范围，不能直接修改。
- 已在 Linear AGE-58 同步该补充。

验证：
- 文档级补充，无代码测试。

遗留问题：
- 后续实现需要设计 editable range 生成策略、patch 应用器和诊断恶化回退机制。

## 2026-05-14 18:26:20 CST - AGE-58 小片段关联修复设计补充

完成内容：
- 补充 AGE-58 修复方案中“小片段 LLM 修复”的关键边界：小片段不是只给 LLM 局部十几行，而是“编辑范围小、只读上下文大”。
- 新增 `CodeRepairContext` 草案，包含编辑行号范围、局部代码、所在符号、文件 outline、诊断、相关片段、路径候选和来源页。
- 明确关联修复流程：先输出修复计划和依赖判断，再生成一个或多个 scoped patch；不能因为需要上下文就退回整文件 rewrite。
- 约束证据不足的业务逻辑问题必须保留 unresolved / OCR-Q，不允许 LLM 强行猜。
- 已在 Linear AGE-58 同步该补充。

验证：
- 文档级补充，无代码测试。

遗留问题：
- 后续实现时需要决定 `CodeRepairContext` 的 parser/linter 数据来源，以及多语言 file outline 的最小可行实现。

## 2026-05-14 18:14:49 CST - AGE-58 代码模式质量修复方案

完成内容：
- 新增 `docs/zh/backend/age-58-code-mode-quality-plan.md`，把代码模式质量修复拆成质量可观测性、column segment 建模、路径候选置信度、UI 噪声过滤、确定性 OCR 清理、诊断驱动 LLM、小片段修复、二次裁剪 OCR 和可选代码库上下文。
- 明确修复主线不依赖参考源码，必须泛化到多项目、多语言；参考源码匹配只作为可插拔增强，不绑定 Chromium、C/C++ 或任何固定项目结构。
- 修正质量判断口径：多页归并到少量源文件对大文件是合理现象，`merged_pages` 只作为风险信号，不能单独作为失败条件。
- 更新后端文档索引，新增 AGE-58 方案入口。
- 更新已知问题，记录“代码模式质量不能只看归并文件数量”的复用经验。
- 已在 Linear AGE-58 补充修复方案文档位置、设计口径和推荐实施顺序。

验证：
- 已核对 `docs/zh/architecture.md`、`docs/zh/backend/age-8-ide-code.md`、`docs/zh/backend/age-8-robustness-report.md`、`docs/zh/known-issues.md` 和代码模式相关实现模块后撰写方案。

遗留问题：
- 尚未进入实现；推荐后续先做 Phase 0-2：代码质量报告、`CodeSegment` / `PathCandidate`、UI 噪声过滤与确定性 OCR 清理。

## 2026-05-14 17:45:38 CST - AGE-58 代码模式质量偏差排查

完成内容：
- 对比 `/mnt/TrueNAS_Share/chromium/chromium_decode/code` 原始 IDE 照片、`/tmp/docrestore_b5950355` OCR 中间产物与 `files/` 最终代码产物，确认最终偏差不是单一 OCR 识别率问题。
- 定位主要质量损失来源：整屏 OCR 混入 VSCode 顶栏、breadcrumb、搜索框、Loading 遮罩、底部 Terminal/Marketplace 等 UI 噪声；双栏 IDE 截图被按不稳定路径 OCR 过度归并；`files-index.json` 中多个文件带 `code.refine.truncated`，LLM 修复基本未能作用于大文件。
- 抽样确认 PP-OCR basic 对可见代码行能提供可用行级 bbox 与部分文本，但暗色主题小字号、红色语法高亮、拍摄透视和低对比会造成 `//`、下划线、大小写、引号、括号和标点的系统性错误。
- 已在 Linear AGE-58 补充排查结论与分阶段优化方案，后续应优先做版面裁剪、路径/分组置信度和编译驱动修复，而不是只切换 OCR 引擎。

验证：
- 抽样查看 `DSC06835.JPG`、`DSC06853.JPG`、`DSC07032.JPG` 原图，分别覆盖双栏初始页、右侧头文件 Loading 遮罩、搜索框遮挡与后段行号页。
- 抽样读取对应 `text_lines.jsonl`、`files-index.json` 和最终 `openmax_video_decode_accelerator.cc`、`openmax_video_decode_accelerator.h`、`BUILD.gn`、`gles2_dmabuf_to_egl_image_translator.cc` 产物进行交叉比对。

遗留问题：
- AGE-58 仍需实现代码模式质量门禁、双栏裁剪重 OCR、路径候选校正、行号连续性分组和编译/LLM 小片段修复；本次仅完成归因和方案同步。

## 2026-05-14 16:41:51 CST - 修复代码模式未切 PP-OCR basic 导致 text_lines 为空

完成内容：
- 定位失败原因：代码模式的 fail-fast 校验本身正确，问题是前端只提交 `code.enable=true`，没有把默认 PaddleOCR 任务显式切到 `paddle_pipeline="basic"`；后端默认 `vl` pipeline 不产出 `PageOCR.text_lines`，因此 272 页全部被判定为缺少行级 bbox。
- `OCRConfigRequest` 新增 `paddle_pipeline: Literal["basic", "vl"] | None`，允许请求级覆盖 PaddleOCR pipeline。
- `TaskForm` 在默认 PaddleOCR 且开启代码模式时提交 `ocr.model="paddle-ocr/ppocr-v4"` 与 `ocr.paddle_pipeline="basic"`；选择 DeepSeek 等非 PaddleOCR 引擎时不强塞 Paddle 专用字段，保留 OCR 抽象边界。
- 补充前端回归测试，锁住“勾选代码模式 → 默认 PaddleOCR 请求 basic pipeline”的行为。
- 更新已知问题文档，明确 `text_lines` 校验与前端显式 basic 请求的关系。

验证：
- `npm exec vitest -- --run tests/components/TaskForm.test.tsx`：通过，1 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `python -m pytest tests/api/test_create_task_code_mode.py -q`：通过，9 passed；pytest-asyncio 输出 loop scope deprecation warning。
- `ruff check backend/docrestore/api/schemas.py tests/api/test_create_task_code_mode.py`：通过。

遗留问题：
- “预加载引擎”接口当前只按 model/GPU 判断 ready，没有把 `paddle_pipeline` 暴露到状态响应；即使预热了 VL，任务提交 basic 后 EngineManager 仍会按 pipeline 维度切换，不影响正确性，但预热提示还不够精确。

## 2026-05-13 17:39:03 CST - AGE-57 代码模式 Review 窗口 IDE 化

完成内容：
- `CodeViewer` 从纯 `<pre>` 升级为轻量 IDE 视图：代码区显示 gutter 行号、按语言 token 着色，并根据 `compile_failing_lines` 高亮编译/语法失败行。
- 新增代码文件在线编辑入口，编辑后通过 `PUT /tasks/{task_id}/files/{file_path}` 保存回 `output_dir/files/`；后端路径校验沿用代码文件读取边界，只允许写已存在的代码产物。
- 保存成功后同步更新前端内存索引与后端 `files-index.json` 的 `line_count` / `line_no_range`，避免文件列表行数长期显示旧值。
- 抽出 `features/task/codeSyntax.ts` 维护轻量语法 token 化逻辑，覆盖 C/C++、Python、JS/TS、Shell、JSON/Markdown 等常见代码模式输出。
- 补充中英文/繁中文案，完善桌面和窄屏样式；窄屏下代码路径独占一行，避免被编辑按钮挤压断行。

验证：
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx`：通过，4 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `python -m pytest tests/api/test_code_mode_e2e.py -q`：通过，3 passed；pytest-asyncio 输出 loop scope deprecation warning。
- `ruff check backend/docrestore/api/routes.py backend/docrestore/api/schemas.py backend/docrestore/api/errors.py tests/api/test_code_mode_e2e.py`：通过。
- Vite dev server + Playwright 视觉验证：已生成 `screenshots/current.png` 和 `screenshots/current-mobile.png`；桌面与移动宽度均无横向溢出，错误行、语法着色、行号和编辑按钮显示正常。

遗留问题：
- 本次视觉验证使用注入的代码模式 DOM fixture 覆盖样式；因 FastAPI 后端未启动，Vite 首页请求 `/api/v1/tasks`、`/api/v1/gpus`、`/api/v1/ocr/status` 出现 502，这不影响代码视图样式验证。后续可增加真实已完成代码模式任务 fixture 做端到端截图。
- 轻量语法着色不是完整语言解析器，不能跨行识别块注释/多行字符串；当前定位是 Review 窗口可读性增强，复杂 IDE 语义能力仍建议后续接入成熟编辑器库。

## 2026-05-13 11:03:00 CST - 修复多文档预览路径型 page marker 同步回归

完成内容：
- 定位回归原因：共享原图列表统一把左侧源图 `data-page` 归一化为裸文件名，但 Markdown 的 `<!-- page: ... -->` 若带相对路径，右侧锚点仍使用原始路径，导致左右锚点 key 不一致。
- `injectPageAnchors()` 改为用同一套 `imageNameToPageKey()` 归一化 page marker，保证 `section/p2.jpg` 注入为 `data-page="p2.jpg"`。
- `filterImagesForDoc()` 同时支持 page marker 原文和归一化后的 page key 匹配，保留带输入子目录的多文档过滤能力。
- 补充回归测试，覆盖带相对路径的 page marker 源图过滤和 Markdown 锚点归一化。

验证：
- `npm exec vitest -- --run tests/components/DocCodePreview.test.ts tests/features/task/markdown.test.ts tests/hooks/useScrollSync.test.ts`：通过，30 个测试。
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx`：通过，2 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。

遗留问题：
- 若同一个任务中不同输入子目录存在同名图片，且 page marker 只保留裸文件名、`doc_dir` 又无法提供输入目录线索，前端仍只能匹配到全部同名页；需要后端在 page marker 中稳定保留输入相对路径才能彻底消除歧义。

## 2026-05-12 23:18:00 CST - 抽象预览滚动与原图列表复用

完成内容：
- 新增 `SourceImageList` 共享组件，统一文档模式与代码模式的原图列表渲染、`data-page` 锚点写入和点击放大 lightbox 行为。
- 新增 `sourceImagePreview` 数据模型工具，集中维护源图文件名到 page key 的转换规则，避免两个模式各自拆文件名。
- 新增 `usePreviewScrollSync`，把预览场景固定为 `continuous` 同步策略；文档模式与代码模式都通过该 hook 接入底层 `useScrollSync`。
- `SourceImagePanel` 收敛为文档模式外壳组件，底层列表复用 `SourceImageList`；`CodeViewer` 移除内联原图列表和 lightbox 实现。

验证：
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx tests/components/DocCodePreview.test.ts tests/hooks/useScrollSync.test.ts`：通过，16 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `pytest tests/output/test_code_renderer.py -q`：通过，8 passed, 1 skipped。
- `ruff check backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- `mypy --strict backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- Vite dev server + FastAPI + 本地 `age50-fixture` 任务完成 Playwright 验证：文档模式源图列表存在共享锚点；代码模式代码正文与原图列表锚点一致，双向滚动同步仍生效；页面无 console error。

遗留问题：
- 当前抽象覆盖原图列表与同步策略；代码正文锚点生成仍保留在 `CodeViewer`，因为它依赖代码模式专有的 `source_page_ranges` 与文件内容行号。

## 2026-05-12 22:43:00 CST - 代码模式结果与原图同步滚动

完成内容：
- `files-index.json` 新增向后兼容的 `source_page_ranges` 字段，记录每个 `source_pages` 对应来源页的起止行号，供前端把代码位置映射回原图。
- 代码模式 `CodeViewer` 复用 `useScrollSync`：代码正文按来源页行号插入隐形 `data-page` 锚点，右侧原图使用同名锚点，实现代码与原图双向同步滚动。
- 旧代码模式任务没有 `source_page_ranges` 时，前端按 `source_pages` 顺序均分代码正文生成锚点，保留历史产物可预览性。
- 代码模式窄宽度布局改为纵向堆叠，避免三栏网格把代码栏挤到不可读，保证同步滚动区域仍可操作。
- 补充前端组件测试与后端 renderer 测试，覆盖新索引字段、代码/原图同名锚点和旧索引兼容。

验证：
- `pytest tests/output/test_code_renderer.py -q`：通过，8 passed, 1 skipped。
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx tests/hooks/useScrollSync.test.ts`：通过，12 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `ruff check backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- `mypy --strict backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- Vite dev server + FastAPI + 本地 `age50-fixture` 代码模式任务完成 Playwright 视觉验证；窄宽度和桌面宽度布局正常，页面无 console error。浏览器实测代码正文与原图列表均有同名 `data-page` 锚点，窄宽度下代码滚动会同步原图列表，原图滚动会同步代码位置。

遗留问题：
- `source_page_ranges` 对大 gap 折叠后的显示行映射仍是基于原始行号的近似位置；如后续需要像 IDE 一样逐行精确，可在后端索引中输出渲染后行号映射。

## 2026-05-12 14:01:38 CST - 前端预览连续滚动同步优化

完成内容：
- 将文档预览的源图/Markdown 同步滚动从顶部跳转式 `start` 对齐改为 `continuous` 连续映射。
- `useScrollSync` 新增基于视口中心的 page 区间比例映射：屏幕中间文本落在当前 page 的哪个相对位置，源图列表就滚到同 page 区间的对应位置，并保持在视口中间。
- 最后一页没有下一锚点时，使用锚点元素高度或剩余内容高度计算连续比例，避免末页同步退化为固定位置。
- 程序化滚动防递归标记改为只标记被同步侧，避免用户连续滚动源侧时后续 scroll 事件被 150ms 窗口吞掉。
- 补充 `continuous` 模式单元测试，覆盖比例映射和连续滚动源侧不被阻断。

验证：
- `npx vitest run tests/hooks/useScrollSync.test.ts`：通过，10 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- Vite dev server + Playwright 截图验证首页正常渲染，控制台无 warning/error。当前没有可直接复现的已完成预览任务页面，滚动行为以 hook 单元测试覆盖。

遗留问题：
- 若后续需要更贴近真实视觉，可增加带已完成任务 fixture 的前端集成页或 e2e 测试，用真实源图高度和 Markdown 锚点验证滚动同步手感。

## 2026-05-12 18:36:14 CST - 代码模式 OCR 解耦与失败状态持久化

完成内容：
- 移除 `code.enable=true` 时 API / `PipelineConfig` 对 PaddleOCR `paddle_pipeline` 的自动改写；代码模式改为依赖 `PageOCR.text_lines` 抽象契约。
- 代码模式在未获得任何行级 OCR 输出时明确失败，提示当前 OCR 引擎缺少 `PageOCR.text_lines`，避免静默产出空源文件。
- retry / resume 新任务继承原任务 `CodeRestoreConfig`；对旧 bug 产生的无 code 快照任务，从 `files-index.json` 或 `files/` 代码产物做最小兼容推断。
- `task_results` 增加子文档级 `error` 持久化，重启后可恢复部分失败 tab 的错误状态。
- 更新架构文档与已知问题，明确代码模式的 OCR 抽象边界。

验证：
- `pytest tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/persistence/test_database.py tests/pipeline/test_task_manager.py tests/pipeline/test_code_mode_ocr_contract.py`：通过，64 个测试。
- `ruff check ...`（本次改动相关文件）：通过。
- `mypy --strict ...`（本次改动相关后端与测试文件）：通过。
- `bash scripts/check_quality.sh`：通过；895 passed, 73 skipped。前端 lint 仅输出 ESLint 多 tsconfig 性能提示。

遗留问题：
- 若未来引入新的 OCR 引擎，需要在对应引擎中填充 `PageOCR.text_lines` 才能用于代码模式。

## 2026-05-12 20:13:49 CST - 修复多子文档预览源图过滤

完成内容：
- 定位 `58c941046bab5159d695025d7bf9b15765534fa7` 后多子文档部分 tab 不滚动的问题：前端只按 `doc_dir/` 过滤源图，但边界检测产生的 `doc_dir` 是输出子目录名，不一定对应输入图片路径前缀。
- 新增 `features/task/sourceImages.ts`，优先从当前子文档 Markdown 的 `<!-- page: ... -->` 标记提取页文件名来匹配源图。
- 对 `section/输出标题` 这类“输入子目录 + 边界拆分输出目录”场景，逐级剥掉尾部输出目录后再匹配源图前缀。
- 保留无 page marker 时按 `doc_dir/` 前缀过滤的旧行为。
- 补充前端单元测试，覆盖扁平边界拆分、子目录内边界拆分、无页标记兼容和页标记去重。

验证：
- `npx vitest run tests/components/DocCodePreview.test.ts tests/hooks/useScrollSync.test.ts tests/features/task/markdown.test.ts`：通过，28 个测试。
- `npx vitest run`：通过，57 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- Vite dev server + Playwright 首页截图完成；因后端服务未启动，控制台出现 `/api/v1/tasks`、`/api/v1/gpus`、`/api/v1/ocr/status` 的 502，不影响本次前端渲染检查。

遗留问题：
- 当前没有可直接加载的已完成多子文档任务 fixture，真实任务页的滚动手感仍建议后续用 e2e fixture 覆盖。

## 2026-05-14 23:08:19 CST - AGE-64 代码模式 OCR 后处理与 UI 噪声过滤

完成内容：
- 新增 `clean_code_ocr_text()`，在代码模式渲染前过滤 IDE/VS Code 强信号 UI 噪声行，并以空行保留原始行数。
- 保留 `correct_ocr_artifacts()` 兼容入口，扩展语言感知规则：`//` 注释前缀错识恢复、C/C++ 预处理指令大小写规范化。
- 代码模式 pipeline 接入后处理结果 flags，`files-index.json` 与 `.quality_report.json` 可追踪 UI 噪声过滤、孤立 OCR 伪字形过滤和行数保持。
- 补充多语言与安全回归测试，避免 Python 注释语义、字符串字面量和普通代码行被误过滤。

验证：
- `python -m pytest tests/processing/test_ocr_postfix.py tests/pipeline/test_quality_report.py tests/output/test_code_renderer.py tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，78 passed, 1 skipped。
- `ruff check backend/docrestore/processing/ocr_postfix.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/processing/test_ocr_postfix.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- 当前只做确定性强规则过滤；弱信号 UI 噪声和跨行语义修复留给 AGE-65/AGE-66 的关联与全文修复链路处理。

## 2026-05-14 23:20:50 CST - AGE-65 多语言语法诊断接入

完成内容：
- 新增 `CodeDiagnosticRunner`，支持 Python/JSON/TOML/XML/YAML 标准解析与 JS/TS/C/C++/Go/Rust 外部工具诊断；工具缺失降级为 `tool_unavailable`。
- 代码模式 pipeline 启用诊断，renderer 将新 `diagnostic` 对象和旧 `compile_status`、`compile_failing_lines` 等兼容字段写入 `files-index.json`。
- `.quality_report.json` 新增 `code_diagnostic` 阶段，记录语法失败、语义失败、工具缺失和诊断运行失败。
- 补充 fake 工具链单测，覆盖工具存在、工具缺失、语法失败行号提取、语义失败分类、索引回填与质量报告记录。

验证：
- `python -m pytest tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，46 passed, 1 skipped。
- `ruff check backend/docrestore/processing/code_diagnostics.py backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- 当前诊断是单文件低成本检查，不加载项目完整依赖图；`semantic_dirty` 更多表示缺上下文或依赖，后续修复窗口应优先使用 `syntax_dirty` 行。

## 2026-05-14 23:31:22 CST - AGE-66 诊断驱动 scoped code repair

完成内容：
- 新增 `CodeRepairContext` / `DiagnosticCodeRepairer`，基于语法诊断失败行生成小编辑窗口，prompt 携带只读上下文、outline、来源页、路径候选和相关片段。
- LLM scoped repair 输出修复计划、依赖判断和 JSON patch；patch 只能落在 `edit_range` 内，越界或解析失败即回退该窗口。
- patch 应用后重新运行轻量诊断；诊断结果恶化时回退 patch，证据不足时保留 unresolved。
- 代码模式 LLM 前新增预诊断：有 `syntax_dirty` 行时走 scoped repair；大文件缺少诊断窗口时跳过整文件 LLM，避免常态 `code.refine.truncated`。
- 质量摘要与质量报告补充 `code.repair.*` 风险标记。

验证：
- `python -m pytest tests/llm/test_code_repair.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_code_mode_e2e.py -q`：通过，68 passed, 1 skipped。
- `ruff check backend/docrestore/llm/code_repair.py backend/docrestore/llm/code_refine.py backend/docrestore/llm/prompts.py backend/docrestore/processing/code_diagnostics.py backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/llm/test_code_repair.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- 当前 scoped repair 的相关片段选择仍是同目录启发式；更强的跨文件符号关联可在后续 issue 中基于 outline / import/include 图继续增强。

## 2026-05-15 04:05:47 CST - AGE-67 小段修复后的全文件一致性审计 pass

完成内容：
- 新增 `CodeConsistencyAuditor` 与 `CodeConsistencyAuditContext`，在 scoped repair 后读取全文件 outline、symbol table、只读摘录、诊断、未解决项与重复 OCR 混淆。
- 审计 prompt 只允许输出绑定行号的受限 JSON patches；未授权范围的问题只能返回 `candidate_ranges`。
- patch 应用前校验必须落在 `editable_ranges` 内；试图修改只读范围、解析失败或输出截断会回退。
- patch 应用后重新运行轻量诊断；诊断恶化时回退该 patch。
- pipeline 在诊断驱动 scoped repair 后追加全文件一致性审计，质量摘要与质量报告补充 `code.audit.*` 标记。

验证：
- `python -m pytest tests/llm/test_code_repair.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_code_mode_e2e.py -q`：通过，74 passed, 1 skipped。
- `ruff check backend/docrestore/llm/code_repair.py backend/docrestore/llm/prompts.py backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/llm/test_code_repair.py`：通过。

遗留问题：
- 当前 repeated OCR confusion 使用字符归一启发式生成候选行；后续可结合语言 parser token 与项目符号索引降低误报。

## 2026-05-15 04:12:38 CST - AGE-68 代码 column 裁剪增强二次 OCR

完成内容：
- 新增 `code_column_ocr` 模块，基于 `IDELayout` 行号锚点和 column 行生成 per-column crop bbox。
- crop 预处理支持灰度、自适应对比度、对比度增强、锐化和 2x/3x 放大，并将 crop OCR bbox 回映射到原图坐标系。
- 代码模式 pipeline 增加可选 `secondary_column_ocr`，开启后先用整图 OCR 识别 layout，再对每个 column 裁剪增强重跑 OCR，保留 tab/sidebar/terminal 等首轮 layout provenance。
- API 请求与 `CodeRestoreConfig` 增加二次 OCR 配置项；默认关闭，避免不支持临时 crop OCR 的环境增加成本或失败。
- 补充单测覆盖裁剪边界、增强缩放、bbox 回映射和双栏 crop OCR 集成。

验证：
- `python -m pytest tests/processing/test_code_column_ocr.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/api/test_code_mode_e2e.py tests/output/test_code_renderer.py -q`：通过，36 passed, 1 skipped。
- `ruff check backend/docrestore/processing/code_column_ocr.py backend/docrestore/pipeline/config.py backend/docrestore/api/schemas.py backend/docrestore/pipeline/pipeline.py tests/processing/test_code_column_ocr.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py`：通过。

遗留问题：
- 当前二次 OCR 默认关闭；真实数据集需要开启 `code.secondary_column_ocr=true` 后做抽样对比，确认不同 OCR 引擎对 crop 临时图的行级输出质量。

## 2026-05-15 04:21:44 CST - AGE-69 可选 CodeContextProvider

完成内容：
- 新增离线 `CodeContextProvider` Protocol 与 `LocalCodeContextProvider`，支持本地参考源码目录的 `list_files`、`search_paths`、`search_snippets`。
- 支持多语言文件发现、shebang 识别、路径 fuzzy match、片段检索，并默认跳过 `.git`、`node_modules`、build/cache 等目录。
- `CodeRestoreConfig` / API 请求增加 `context_root`，默认空字符串关闭；非法或空路径返回 None，主流程不受影响。
- pipeline 在提取 IDE meta 后追加 `reference` 来源的 `PathCandidate`，不覆盖 OCR path。
- scoped repair / consistency audit 的 `related_snippets` 接入参考源码片段，作为只读证据传入 LLM prompt。

验证：
- `python -m pytest tests/processing/test_code_context.py tests/llm/test_code_repair.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/api/test_code_mode_e2e.py -q`：通过，43 passed。
- `ruff check backend/docrestore/processing/code_context.py backend/docrestore/llm/code_repair.py backend/docrestore/pipeline/config.py backend/docrestore/api/schemas.py backend/docrestore/pipeline/pipeline.py tests/processing/test_code_context.py tests/llm/test_code_repair.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py`：通过。

遗留问题：
- 当前 snippet 检索基于 token 命中和文件路径启发式；后续可加离线 symbol index 和 import/include 图提高召回质量。

## 2026-05-15 04:26:34 CST - AGE-58 代码模式质量改进子任务收口

完成内容：
- AGE-61 至 AGE-69 子 issue 已全部完成并在 Linear 标记 Done。
- 已提交并评论每个子 issue 的 commit URL，覆盖质量报告、来源建模、文件分组、噪声过滤、多语言诊断、scoped repair、全文一致性审计、column 二次 OCR 和可选代码库上下文。
- AGE-69 提交 `b0e87bcf0cba5e894384829e504628de079629c8`，补齐离线 `CodeContextProvider` 后，AGE-58 当前实现路径已闭环。

验证：
- AGE-69 相关测试：`python -m pytest tests/processing/test_code_context.py tests/llm/test_code_repair.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/api/test_code_mode_e2e.py -q`：通过，43 passed。
- AGE-69 相关 ruff：通过。
- AGE-69 commit hook：`mypy --strict`、`ruff`、`typos` 通过。

遗留问题：
- AGE-58 父 issue 建议进入 review/验收阶段；需要用真实代码图片任务开启 `secondary_column_ocr` 与可选 `context_root` 后跑一轮对比，确认 OCR 偏差、路径合并和 LLM 修复质量是否达到预期。

## 2026-05-19 00:17:22 CST - 代码诊断审查标注与依赖错误降级

完成内容：
- `CodeDiagnostic` 增加 `items` 结构化行级标注和 `dependency_errors`，用于前端审查波浪线与 tooltip。
- C/C++ 诊断 target 支持 `include_root`，renderer 与内存诊断会把输出根目录传给 `gcc/g++ -I`，避免生成文件间的本地 include 被误判为缺失。
- 缺失头文件类错误分类为 `dependency_dirty` / `dependency`，不再作为 `syntax_dirty` 驱动 LLM 语法修复。
- files-index 前端 schema 增加 `diagnostic` 和 `diagnostic.items`；CodeViewer 使用结构化诊断渲染红色/黄色/语义波浪线和行 tooltip。
- 用 `/tmp/docrestore_02bca34c` 的 `openmax_video_decode_accelerator.cc` 验证：诊断越过本地生成头文件，停在外部 `base/compiler_specific.h`，状态为 `dependency_dirty`。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py -q`：通过，20 passed, 1 skipped。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py backend/docrestore/output/code_renderer.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py`：通过。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `npx vitest run tests/components/CodeViewer.test.tsx`：通过，5 passed。
- Playwright 打开 `http://127.0.0.1:5173/` 完成截图验证；页面自身 API 502 来自未启动后端，不影响本次静态 UI 渲染检查。

遗留问题：
- 第二轮 stub header 复诊断尚未实现；当前只把真实缺失依赖降级为审查标注。后续可在 dependency pass 后生成空 stub 继续暴露被 include 阻挡的真实语法 OCR 错误。
- GN/BUILD 文件仍缺专门诊断与文件名大小写归一，可作为下一步独立优化。

## 2026-05-19 12:28 CST - 代码模式多语法错误复诊断

完成内容：
- `CodeDiagnosticRunner` 增加恢复式复诊断：首轮语法错误后，在临时副本中屏蔽已定位错误行并重复运行解析器/工具，继续收集后续独立语法错误。
- Python/JSON/TOML 解析型诊断改为多轮收集；Python 对疑似复合语句头会同步清空缩进 suite，避免残留缩进错误遮挡后续顶层错误。
- C/C++、JS/TS、Go、Rust 等外部工具诊断在语法错误场景下复跑临时副本，合并去重后的多条 `diagnostic.items`。
- 前端 `CodeViewer` 增加多条 syntax diagnostic 回归测试，确认多处红色波浪线和 tooltip 都能渲染。
- `docs/zh/known-issues.md` 新增“代码语法诊断不能只停在首个错误”条目，沉淀本次处理策略。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，12 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，6 passed。
- `npm run lint`：通过。
- `npm run typecheck`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。

遗留问题：
- 复诊断是行级恢复策略，能暴露后续独立语法错误，但对跨多行未闭合括号/字符串等强耦合错误仍可能只能保留已收集结果。

## 2026-05-19 18:27 CST - 代码编辑实时诊断与可接受标注

完成内容：
- 修正复诊断策略：C/C++ 缺失 include 这类 dependency 行也会在临时副本中屏蔽后继续检查，避免只停在第一个头文件错误。
- 新增 `POST /tasks/{task_id}/code-diagnostics`，对代码模式源文件草稿做只读实时诊断，不保存文件。
- `CodeViewer` 编辑态增加 350ms debounce 实时语法检查；诊断结果同步到行号 gutter 和诊断列表。
- 用户可按条“接受此诊断”，例如代码片段中可接受的缺失头文件；接受记录按任务、文件、行内容和诊断信息写入浏览器 localStorage，并可一键恢复。
- 保存代码文件后会把当前实时诊断回写到前端索引状态，避免保存后仍显示旧诊断。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，13 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py backend/docrestore/api/routes.py backend/docrestore/api/schemas.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，7 passed。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。
- Vite + Playwright 打开 `http://127.0.0.1:5173/` 并截图 `docrestore-code-diagnostics-home.png`，控制台无 error。

遗留问题：
- 编辑态红色/黄色标注当前落在 gutter 和诊断列表；原生 textarea 内部无法直接画逐行红色波浪线，若要做到完全 IDE 式内联波浪线，需要后续替换为 CodeMirror/Monaco 等编辑器组件。

## 2026-05-20 18:27 CST - OCR 中文噪声语法标注补强

完成内容：
- 复现 `/tmp/docrestore_02bca34c/files/media/gpu/openmax/gles2_dmabuf_to_egl_image_translator.cc` 漏标：当前诊断只返回 3 个缺失 include，未到第 90 行 `if(hEglImage 二 EGL_NO_IMAGE_KHR){ 王`。
- 在工具诊断后追加不依赖编译器的 OCR 噪声词法扫描：忽略注释、块注释和字符串，只扫描代码区 CJK / 全角字符。
- 噪声扫描结果以 `syntax` / `ocr_noise_non_ascii` 合并进 `diagnostic.items`；即使编译器被 include 或语义错误短路，也能标出代码区中文/全角 OCR 噪声。
- 前端补充回归：接受 include 依赖诊断后，后续 OCR 噪声语法诊断仍保留显示。
- 真实文件验证：`gles2_dmabuf_to_egl_image_translator.cc` 现在返回 line 90、column 15、`ocr_noise_non_ascii`，消息包含 `OCR noise character '二' appears in code`。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，14 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，8 passed。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。

遗留问题：
- 词法扫描目前只报每行第一个 OCR 非 ASCII 噪声字符；同一行多个噪声可通过修复第一处后再次诊断暴露。

## 2026-05-20 18:35 CST - 编译器复诊断 include stub 落地

完成内容：
- 确认上一版并未真正生成缺失头文件 stub，只是错误地把 dependency 行号当作当前 `.cc` 行去屏蔽；对 include 链路里的缺失头文件无效。
- `_collect_additional_tool_diagnostics` 改为从 `missing_include` 诊断消息提取头文件路径，在临时 `__include_stubs__` 下生成 stub header，并把该目录加入编译器 `-I`。
- C/C++ 复诊断增加临时 prelude header，提供常见 EGL/GLuint 等占位类型，减少缺依赖造成的无效阻塞。
- 复诊断现在会迭代新增缺失 include stub，同时继续收集编译器后续语法错误，再把 dependency 和 syntax items 合并去重。
- 真实文件验证：`gles2_dmabuf_to_egl_image_translator.cc` 诊断从只返回 3 个 dependency，变为包含 include dependency、编译器后续 syntax errors，以及 line 90 的 `expected unqualified-id before 'if'` 和 `ocr_noise_non_ascii`。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，14 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，8 passed。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。

遗留问题：
- 生成的 stub header 只为复诊断暴露更多语法错误，不代表依赖语义真实存在；前端仍应允许用户把缺依赖标注为可接受。

## 2026-05-20 19:44 CST - CodeLLMRefiner 分块避免 token 截断

完成内容：
- 修复代码模式 `CodeLLMRefiner` 整文件 refine 容易触发 `finish_reason=length, raw_len=0` 的问题。
- `refine` 模式新增按行/字符数自动切块：超过 80 行或 3500 字符的 SourceFile 分块调用 LLM，避免输出 JSON 超出 provider token 上限。
- 每个 chunk 仍保持行数守恒；单个 chunk 截断、JSON 失败或行数变化时，只回退该 chunk，不再导致整个 SourceFile 回退原文。
- 分块结果会合并 corrections / unresolved，并按原文件行号偏移；flags 增加 `code.refine.chunked=N`、`code.refine.chunk_truncated=i` 等审计信息。
- `rewrite` 模式暂不自动分块，因为 rewrite 允许重排行，盲切会破坏语义边界。
- 已更新 `docs/zh/known-issues.md`，记录 CodeLLMRefiner 整文件截断处理策略。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/llm/test_code_refine.py -q`：通过，21 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/llm/test_code_refine.py tests/llm/test_code_repair.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py -q`：通过，66 passed, 1 skipped。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/llm/code_refine.py backend/docrestore/processing/code_diagnostics.py backend/docrestore/api/routes.py backend/docrestore/api/schemas.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py`：通过。

遗留问题：
- 当前分块按行数/字符数切，不做函数级语法边界识别；后续可用诊断窗口或轻量 parser 进一步按函数/类边界切分。
