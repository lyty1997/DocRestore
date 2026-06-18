# 文档模式编辑器数学公式渲染 — 设计

> 状态：**已确认（分两期）· 方案 B（自定义节点）· 阶段 1+2 均已实现** · 2026-06-18
> 关联：预览侧公式渲染已落地（`tech-stack.md` / `markdownSanitize.ts`），本文只覆盖
> **编辑模式**（Tiptap WYSIWYG），PPT 模式无编辑器、不在范围内。
>
> 用户拍板：① 接受"阶段 1 先保真、阶段 2 再渲染"分期；② 渲染采用**方案 B**（自定义 Math
> 节点 + KaTeX，与预览侧同栈），不用官方扩展。
> **阶段 1 已落地**：`mathNodes.ts`（`MathInline`/`MathBlock` atom 节点）+ `markdownRoundtrip.ts`
> 公式抽取与 turndown 还原 + 编辑器注册 + `mathRoundtrip.test.ts`（含 Tiptap 全链路幂等）。
> 当前编辑器内公式以源码态 `$...$` 显示、不渲染，但 round-trip 逐字保真。

## 1. 背景与目标

预览侧（`DocCodePreview`）已用 `remark-math` + `rehype-katex` 渲染 `$...$` / `$$...$$`。
但文档模式的**编辑器**（`MarkdownWysiwygEditor`，Tiptap/ProseMirror）走的是另一条
链路（`markdownRoundtrip.ts`：`marked` 做 md→html、`turndown` 做 html→md），公式目前
以纯文本形式存在，既不渲染、round-trip 还可能被破坏。

目标（按优先级）：
1. **保真**：编辑器加载 → 编辑无关段落 → 保存，公式 LaTeX 一字不变（**这是底线，必须先做**）。
2. **渲染**：编辑器内把公式所见即所得渲染成 KaTeX。
3. **可编辑**：点击公式可改其 LaTeX 源码，失焦重渲染。

## 2. 现状与风险

| 环节 | 函数 | 对公式的现状/风险 |
|---|---|---|
| md → html | `markdownToHtml`（`marked`） | `marked` 不解析 `$...$`；公式里的 `_` `*` `\` 会被当强调/转义**破坏 LaTeX** |
| Tiptap 编辑 | StarterKit + 自定义 `PageAnchor` | 无 math 节点，公式落进普通段落文本 |
| html → md | `htmlToMarkdown`（`turndown`） | 无 math 规则；`\` `_` 等可能被 turndown 转义 |

> 风险点①（保真）比渲染更紧急：即使不渲染，只要用户在编辑器里存过一次，公式就可能已被
> `marked`/`turndown` 悄悄改坏。所以**阶段 1 先做 round-trip 保真**。

## 3. 方案对比

| 方案 | 做法 | 优点 | 缺点 | 取舍 |
|---|---|---|---|---|
| **A 官方扩展** | Tiptap `@tiptap/extension-mathematics`（v3 起开源，基于 KaTeX） | 现成的 inline/block math 节点 + KaTeX NodeView + 输入规则；与项目 Tiptap 3.22 同源 | 仍需自接 md↔html round-trip（marked/turndown 两侧规则）；需核对其 DOM parse 约定 | **推荐**：渲染/交互不重复造轮子 |
| **B 自定义节点** | 自写 ProseMirror Math Node（atom，inline+block），NodeView 调 `katex.renderToString` | 完全可控、与预览共用 KaTeX 配置 | 节点/NodeView/选区/输入规则全自己维护，工作量大 | A 不可用时的后备 |
| **C 仅保真不渲染** | 不加 math 节点，只在 round-trip 两侧把 `$...$`/`$$...$$` 当不可分原子保护起来 | 改动最小、零渲染风险 | 编辑器里公式仍是源码文本 | 作为**阶段 1** 必做底座 |

推荐：**阶段 1 落 C（保真）→ 阶段 2 在其上叠 A（渲染+交互）**。C 的"把公式当原子保护"正是
A 做 md↔html 映射所需的同一处接缝，不浪费。

## 4. 详细设计（推荐路径）

### 4.1 数据表示
- 行内公式 → ProseMirror inline atom 节点 `mathInline`，属性 `latex: string`。
- 块级公式 → block atom 节点 `mathBlock`，属性 `latex: string`。
- DOM 表示（编辑器内 + round-trip 中介）统一为：
  - 行内：`<span data-math-inline data-latex="...">`（KaTeX 渲染产物塞进去，`data-latex` 存源码）
  - 块级：`<div data-math-display data-latex="...">`
- **唯一真相是 `data-latex`**（原始 LaTeX），渲染产物只是展示；保存时只读 `data-latex`，
  彻底回避"渲染后 HTML 再被 turndown 啃"的问题。

### 4.2 md → html（改 `markdownToHtml`）
在交给 `marked` **之前**先抽取公式（保护，避免 marked 啃 `_`/`\`）：
1. 用 `$$...$$` / `$...$` 正则把公式替换成占位 `<div data-math-display data-latex="...">` /
   `<span data-math-inline data-latex="...">`（`data-latex` 做 HTML 属性转义）。
2. 其余照常 `marked.parse`。
3. （阶段 2）Tiptap 的 math 扩展从这些 `data-math-*` 元素 `parseHTML` 成 math 节点并 KaTeX 渲染。

> 与预览侧 `normalizeDisplayMath` 一致：单行 `$$...$$` 也按块级处理（同一条判定规则可抽公共函数）。

### 4.3 html → md（改 `htmlToMarkdown`）
给 `turndown` 加两条规则（与现有 `pageAnchor`/`htmlComment` 规则并列）：
- `filter` 命中 `data-math-display` 的元素 → 输出 `\n$$\n{data-latex}\n$$\n`。
- `filter` 命中 `data-math-inline` 的元素 → 输出 `${data-latex}`（`$...$`）。
- 关键：只读 `data-latex` 原文，**不** turndown 其内部 KaTeX DOM。

### 4.4 编辑交互（阶段 2）
- 双击 math 节点 → 弹出小输入框编辑 `data-latex`，失焦/回车提交 → 更新节点属性 → NodeView 重渲染。
- 输入规则：行首 `$$` 起新块级公式、行内 `$x$` 自动成节点（A 扩展自带，按需保留）。
- 坏 LaTeX：NodeView 用 `throwOnError:false`，渲红字不崩编辑器（与预览侧一致）。

## 5. round-trip 保真要点（坑）
- `data-latex` 属性必须 HTML 转义（`"` `<` `&`），还原时反转义，否则含 `<`/引号的公式炸属性。
- 公式抽取正则要先 `$$`（块级）后 `$`（行内），且 `$...$` 不跨行；与预览侧 `escapeNonHtmlTags`
  跳过公式区的口径保持一致，必要时把"公式切分"抽成 `features/task/math.ts` 公共函数两边共用。
- 测试必须有 **md → html → md 幂等** 用例：含矩阵 `\\`、下标 `_`、反斜杠命令的公式，
  过一轮 round-trip 后 LaTeX 逐字符不变（镜像现有 `markdownRoundtrip.test.ts`）。

## 6. 工程量与分期
- **阶段 1（保真，~0.5 天）**：`markdownRoundtrip.ts` 两侧加公式原子保护 + turndown 规则 +
  round-trip 幂等测试。**不渲染**，但确保编辑器不再改坏公式。可独立交付。
- **阶段 2（渲染+交互，~1–2 天）**：接 `@tiptap/extension-mathematics`（或自定义节点），
  NodeView KaTeX 渲染 + 双击编辑 + 输入规则；视觉验证（编辑器内公式截图）。
- 判定：分期后每阶段都"刚刚好"——阶段 1 解决最痛的保真，阶段 2 才是体验增强；不一次性堆。

## 7. 风险与未决问题
- A 扩展的 `parseHTML`/`renderHTML` 约定需在实现时核对，可能要把 4.2 的 `data-math-*` 对齐它的
  默认 DOM（否则 parse 不进节点）——这是阶段 2 第一步要验证的。
- 单 `$` 行内公式与正文里字面 `$`（价格）的歧义：与预览侧同源问题，沿用同一判定，不在本设计单独解。
- 是否需要"源码/预览"切换让用户直接编辑整段 LaTeX？暂不做，双击编辑单条已够。

## 8. 已确认决策（2026-06-18 用户拍板）
1. ✅ **分两期**：阶段 1 先保真、阶段 2 再渲染（不一次到所见即所得）。
2. ✅ **方案 B**：自定义 Math 节点 + KaTeX（与预览侧同栈），不用官方 `@tiptap/extension-mathematics`。

## 9. 实施进度
- **阶段 1（保真）✅ 已实现**：
  - `mathNodes.ts`：`MathInline`（inline atom）/ `MathBlock`（block atom），latex 存 `data-latex`。
  - `markdownRoundtrip.ts`：`mathToPlaceholders` 把 `$..$`/`$$..$$` 抽成 `data-math-*` 占位
    （先块后行内、占位非空避开 turndown blank 丢弃）；turndown 加 `mathInline`/`mathBlock` 规则
    只读 `data-latex` 还原。
  - `MarkdownWysiwygEditor.tsx` 注册两节点；`App.css` 加源码态样式。
  - `mathRoundtrip.test.ts`：string 两端 + **Tiptap 全链路**幂等（含 `\\`、下标、`\` 命令）。
- **阶段 2（渲染 + 交互）✅ 已实现**：`mathNodes.ts` 的 `createMathNodeView(displayMode)`
  给两节点加 KaTeX `NodeView`（`katex.render`，`throwOnError:false` / `strict:false`，坏公式
  渲红字不崩）；双击进入编辑（block 用 textarea / inline 用 input，回车提交、Esc 取消、
  block 用 Shift+回车换行），`setNodeMarkup` 更新 `data-latex`。**NodeView 只影响编辑态显示、
  不参与序列化**（`getHTML` 仍走 `renderHTML` 输出 `data-latex`），故阶段 1 的 round-trip 保真
  不受影响。KaTeX CSS 由 `MarkdownWysiwygEditor` 引入（避开单测导入图）。
  测试 `mathNodeView.test.ts`：渲染（编辑器内出现 `.katex` / `.katex-display`）、双击弹源码框、
  回车提交更新属性 + 重渲染 + 序列化同步、坏公式不抛错；Playwright 实测真实编辑器渲染 + 编辑
  截图通过。
  - **未做（可选后续）**：输入规则（typed `$$`/`$x$` 自动成节点）、工具栏「插入公式」按钮——
    当前主场景是编辑 OCR 已产出的公式，新建公式留待需要时补。
