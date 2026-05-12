# 开发进度

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
