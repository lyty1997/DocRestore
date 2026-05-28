# DocRestore Codex 工作流

本文件是 Codex 在本仓库的主指令入口，迁移自 `CLAUDE.md` 与 `claude_workflow/`。对话、说明、提交信息默认使用中文；代码注释和 docstring 使用中文，保留标准英文术语。

## 项目上下文

- 项目名：`docrestore`
- 核心目标：处理一组文档屏幕拍摄照片，还原为原文格式的 Markdown 文档。
- OCR 引擎：PaddleOCR 为主，DeepSeek-OCR-2 作为备用。
- LLM 整理：可配置接口，支持云端 API 和本地 LLM；当前 GPU 主要留给 OCR，LLM 默认走云端。
- 开发优先级：先完成“文档照片 -> Markdown”，后续再迭代“IDE 代码照片 -> 源文件”。

## 文档与事实源

- 文档入口：`docs/README.md`
- 中文文档：`docs/zh/`
- English docs：`docs/en/`
- 开发进度：`docs/zh/progress.md`
- 架构变更前必须查看 `docs/zh/architecture.md` 和对应模块文档。
- 执行工作前先查阅已知问题文档；如果不存在且本次修复沉淀出可复用经验，创建或更新对应已知问题文档。

## 工作方式

- 复杂任务开始前先列出子任务清单，并在执行中维护状态。
- 遇到需要确认或澄清的问题时，优先向用户提问；不要用高风险假设推进。
- 每个方案都要判断是过度工程、欠工程还是刚刚好，并说明依据。
- 代码开发前先核对相关设计文档；重要设计变更应先更新文档。
- 分模块开发时，以总设计文档或架构设计文档为唯一真相源，核对上下游输入输出接口。
- 每个模块对接前，先用虚构参数验证输入输出，拿到证据后再做模块联调。
- 任务结束或中断恢复时，更新 `docs/zh/progress.md`，记录时间戳、主题、完成内容和遗留问题。

## Python 规范

- 所有函数签名必须完整标注参数和返回值类型。
- 禁止无说明使用 `Any`；泛型容器必须标注元素类型，例如 `list[str]`。
- 外部输入（API 请求、用户输入、配置、文件解析结果）必须用 pydantic `BaseModel` 校验。
- 文件、数据库连接、网络会话、锁必须用 `with`、`async with`、`contextlib.closing` 或 `try/finally` 管理。
- 禁止拼接 SQL；禁止 `eval()` / `exec()`，除非有明确安全沙箱和注释说明。
- 子进程调用禁止 `shell=True`，用列表形式传参。
- 日志和输出禁止打印密钥、token、密码等敏感信息。
- 测试使用 `pytest` + `pytest-asyncio`；新功能必须有单元测试，核心模块目标覆盖率不低于 80%。
- 提交或交付前按改动范围运行检查；完整质量门禁统一使用 `bash scripts/check_quality.sh`。

## 并发与资源安全

- 禁止裸 `asyncio.create_task(coro())`；必须保存引用，并在 shutdown 时 `cancel` + `await/gather`。
- `task.cancel()` 后必须 await，避免 pending task 被销毁。
- 长生命周期子进程必须设置 `start_new_session=True`，并在 Linux 下尽量设置 `PDEATHSIG(SIGKILL)`。
- `stdout=PIPE` / `stderr=PIPE` 必须有持续 drain，避免 pipe buffer 写满导致子进程卡死。
- shutdown 顺序固定为：取消用户后台 task、关闭 manager、取消长循环、同步资源清理、关闭 pipeline、关闭 DB。
- 长期线程必须 join；`ThreadPoolExecutor` 必须用 `with` 或 `shutdown(wait=True)`。
- HTTP client、数据库、WebSocket 订阅必须有幂等关闭或取消链路。
- shell 启动脚本必须有 `trap cleanup EXIT INT TERM`，并提供 SIGKILL fallback 与二次 Ctrl+C 强杀路径。

## TypeScript / JavaScript 规范

- `tsconfig.json` 必须启用 strict 系列约束，包括 `strict`、`noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`、`noImplicitOverride`、`noFallthroughCasesInSwitch`、`forceConsistentCasingInFileNames`、`verbatimModuleSyntax`、`noPropertyAccessFromIndexSignature`。
- 禁止 `any`；用 `unknown` + 收窄。禁止 `@ts-ignore`；如确需使用，改用带说明的 `@ts-expect-error`。
- 函数返回值必须显式标注；泛型必须有约束。
- 外部输入、环境变量、文件解析结果必须用 zod schema 校验，并从 schema 派生类型。
- 禁止空 `catch {}`；`catch(err: unknown)` 必须收窄后处理或重新抛出。
- 禁止 fire-and-forget async；必须 `await` 或 `.catch()`。
- 事件监听器、定时器、stream、数据库连接必须在作用域结束时清理。
- 禁止 `eval()`、`new Function()`、`innerHTML`；URL 用 `URLSearchParams`，SQL 用参数化查询。
- 测试优先使用 `vitest`，文件名 `*.test.ts`，异步测试必须 await 断言。

## 前端 UI 验证

每次修改 UI 后必须完成视觉验证：

1. 确认开发服务器正在运行，例如 `npm run dev`。
2. 运行 `node scripts/screenshot.js http://localhost:3000/你修改的页面`。
3. 查看 `screenshots/current.png`。
4. 发现问题立即修复并再次截图。
5. 没有截图验证的 UI 修改视为未完成。

## Markdown 图表

- Markdown 中的结构图、流程图、时序图、状态图统一使用 PlantUML。
- 禁止用 ASCII art、Mermaid、截图或二进制 SVG/PNG 代替可维护图源；极简目录树除外。
- 代码块围栏统一使用 ```` ```plantuml ````。
- 本机默认 jar：`/home/lyty/work/envcfg/plantuml-1.2026.1.jar`。
- 每次新增、修改或调试 PlantUML 图时，必须使用 `.codex/skills/plantuml-in-markdown` 的流程：提取、编译、修复、写回、再次验证。

## 测试数据规则

- 测试图片路径：`test_images/`，包含 `.JPG`、`.jpg` 等格式。
- 禁止在代码、测试、文档中写死特定数据集标识符或正文关键字，例如相机文件名前缀 `DSC*`。
- 需要断言输出包含关键内容时，必须从当前输入派生关键行或关键短语。
- 文件名后缀大小写必须兼容大小写敏感文件系统。

## Git 规范

- `main` 为稳定发布分支，不直接提交。
- `dev` 为开发主干。
- 功能分支格式：`feature/s{N}-{描述}`；修复分支格式：`bugfix/{描述}`；发布分支格式：`release/{版本}`。
- 提交格式：`<type>(<scope>): <中文主题>`。
- `type` 使用 `feat|fix|docs|style|refactor|test|chore`。
- 提交前运行风格检查和测试，并确认无敏感信息。

## Codex 技能

- PlantUML：见 `.codex/skills/plantuml-in-markdown/SKILL.md`。
- 大图预览：见 `.codex/skills/view-gel-image/SKILL.md`。
- Claude hook 的 Codex 替代说明：见 `.codex/hooks/README.md`。
