# DocRestore 项目规范

## 项目信息
- 项目名：docrestore
- 核心功能：处理一组文档屏幕拍摄照片，还原为原文格式的 markdown 文档
- OCR 引擎：PaddleOCR（主）/ DeepSeek-OCR-2（备用）
- LLM 整理：可配置接口，支持云端 API 和本地 LLM
- GPU 限制：仅够跑 OCR，LLM 走云端

## 开发优先级
1. 文档照片 → Markdown（第一版）
2. IDE 代码照片 → 源文件（后续迭代）

## 文档结构
- 文档索引：`docs/README.md`（双语入口）
- 中文文档：`docs/zh/`（架构 / 部署 / 后端 / 前端 / 进度）
- English docs：`docs/en/`（architecture / deployment / backend / frontend）
- 开发进度：`docs/zh/progress.md`

## 开发规范
- 编码前查看 python-coding-rules.md 和 python-testing-rules.md
- 架构变更前查看 `docs/zh/architecture.md` 和对应模块文档

## 质量门禁
- 完整门禁统一入口：`bash scripts/check_quality.sh`，依次跑 `mypy --strict` → `ruff check backend tests scripts` → `typos` → 前端 `npm run typecheck` + `lint` → `pytest --tb=short`，汇总所有失败后整体返回非零。
- 交付或提交前按改动范围跑完该脚本；缺工具视为失败必须补齐，不得跳过。
- Git 提交门禁走 `.pre-commit-config.yaml`（同套检查按文件范围触发）：首次 `pre-commit install`，需要全量复查时 `pre-commit run --all-files`。
- 编辑器实时检查由全局 PostToolUse hook 自动跑（逐文件 mypy/ruff/typos），与上面的全量门禁互补，不互相替代。

## 长任务规范
- 开始复杂任务前，先把所有子任务写入任务清单
- 每完成一个子任务立刻标记 completed
- 被中断后恢复时，先读取任务清单确认进度，再继续未完成的任务

## 测试数据
- 路径：./test_images
- 内容：.JPG .jpg等格式

## 测试规则（重要）
- 禁止在代码/测试/文档中写死特定数据集的标识符或关键字（例如相机文件名前缀 `DSC*`、文档正文关键字等），避免更换测试数据后用例失效。
- 需要断言“输出包含关键内容”时，必须从输入派生断言：
  - 优先从当前测试输入（如 OCR 的 `result.mmd`、README 片段、构造的 markdown）中提取关键行/关键短语；
  - 断言输出包含该派生关键内容，而不是断言某个固定字符串。
- 文件名后缀大小写（`.JPG`/`.jpg`）必须按大小写敏感文件系统兼容处理。
