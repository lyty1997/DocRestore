# Codex 检查替代说明

Claude Code 的 `PreToolUse` / `PostToolUse` hook 不能按原事件机制直接迁移到 Codex。本目录记录等价约束和手动检查命令；实际编码时按 `AGENTS.md` 执行。

## 统一入口

完整质量门禁：

```bash
bash scripts/check_quality.sh
```

Git 提交门禁：

```bash
pre-commit run --all-files
```

如果尚未安装 Git hook：

```bash
pre-commit install
```

## 编辑前约束

- 使用 `apply_patch` 编辑文件。
- 替换文本前必须从文件中读取原文，避免中文标点、空格或不可见字符不匹配。
- 禁止连续两次用完全相同参数重试失败工具调用；失败后先分析原因，再换参数或换方法。

## Python 检查

```bash
mypy --strict path/to/file.py
ruff check path/to/file.py
typos path/to/file.py
pytest --tb=short
```

## TypeScript / JavaScript 检查

```bash
npx tsc --noEmit
npx eslint path/to/file.ts
typos path/to/file.ts
```

## 必须修复的情况

- `mypy`、`ruff`、`tsc`、`eslint`、`typos` 失败。
- 检查工具缺失但该检查对当前改动是必需的。
- 检查超时且无法证明改动安全。
