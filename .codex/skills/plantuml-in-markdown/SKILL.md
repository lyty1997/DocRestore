---
name: plantuml-in-markdown
description: 在 markdown 里嵌入、修改和调试 PlantUML 图。触发场景包括新增图、改图、PlantUML 语法错误或渲染失败。必须完成“选图类型 -> 提取 -> 编译 -> 修复 -> 写回 -> 再验证”闭环。
---

# PlantUML in Markdown

## 必用场景

- 用户要求在 Markdown 中新增或修改 PlantUML 图。
- 用户反馈 PlantUML 无法渲染、预览空白或出现语法错误。
- Markdown 内已有 ` ```plantuml ` 代码块需要调整。

## 工作流

1. 先判断图类型，不要默认使用泳道图。
2. 用脚本提取 Markdown 中所有 `plantuml` 代码块。
3. 用 `java -jar plantuml.jar -failfast2 -pipe` 编译每张图。
4. 根据 stderr 和行号修复 `.puml`。
5. 用写回脚本替换 Markdown 原代码块。
6. 再次全量编译，直到 exit 0 且 PNG/SVG 非空。

## 命令

```bash
bash .codex/skills/plantuml-in-markdown/scripts/extract_and_compile.sh path/to/doc.md /tmp/puml_check
python3 .codex/skills/plantuml-in-markdown/scripts/write_back.py path/to/doc.md /tmp/puml_check
```

默认 jar 路径：

```text
/home/lyty/work/envcfg/plantuml-1.2026.1.jar
```

可用 `PUML_JAR=/path/to/plantuml.jar` 覆盖。

## 图类型选择

| 表达内容 | 正确图类型 |
|---|---|
| 静态结构、分层、组件依赖 | 组件图：`package` + `[component]` |
| 跨组件请求和响应 | 时序图：`participant` + `activate` |
| 单向处理流水线、数据变换 | 活动图 + `partition` |
| 带循环或条件分支的算法 | 活动图：`while` + `if/else` |
| 状态机、生命周期 | 状态图：`state` + 转移 |
| 多参与者且每方都有内部多步 | 泳道图 |

## 关键规则

- 围栏必须是 ` ```plantuml `，不要用 `puml` 或 `uml`。
- 泳道名必须写在一行内；需要换行时使用 `\n`。
- 不要在 `if/else/while` 内切换泳道。
- `note right` 必须紧跟在某个 action 后，不能作为首条语句。
- 时序图中不要对 initiator 重复 `activate`。
- 箭头使用 `->`，不要使用 Unicode 箭头。

## 收工检查

- 每张图类型与表达目标匹配。
- 每张图独立编译通过，stderr 为空。
- 生成的 PNG/SVG 非空。
- Markdown 写回后再次全量编译通过。
