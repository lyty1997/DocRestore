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
  - **工具栏「插入公式」按钮 ✅ 已实现**（2026-06-22）：`mathNodes.ts` 导出 `insertMathNode(editor,
    displayMode)`，工具栏加行内 `$x$` / 块级 `$x$▦` 两按钮（`MarkdownWysiwygEditor.tsx`）。插入空
    math 节点后选中它 → `selectNode` 检测空 latex 自动进入编辑（免再双击），三语 i18n
    `editor.insertMathInline`/`insertMathBlock`。测试 `mathNodeView.test.ts` 覆盖插入+自动编辑+提交
    序列化；Playwright 实测真实编辑器：光标处插入、已有公式不被破坏、自动弹编辑框，截图通过。
  - **未做（可选）**：输入规则（typed `$$`/`$x$` 自动成节点）。

---

# 第三期：可视化公式编辑（MathLive 集成设计 · 待确认）

> 状态：**设计待确认** · 2026-06-22 · 关联：MathLive 技术简报 + 替代方案对比 + 4 路对抗式
> 验证（jsdom / NodeView 焦点 / round-trip / 资源，均真包·真浏览器实测）。
> 定位：在已落地的「KaTeX 渲染 + 双击 textarea 改源码」之上**叠加一个可选的可视化编辑形态**，
> 不删现有方案。

## 10. 目标
让**不懂 LaTeX 的用户**在渲染态可视化编辑公式（光标进出分式/根号/上下标 + 虚拟键盘点按钮建
结构）。硬约束不变：① 预览侧 KaTeX 只读渲染一行不动；② math 节点仍以 `data-latex` 为唯一真相、
round-trip 逐字保真不破；③ 现有 textarea 源码编辑**保留**作"精确改源码"通道与回退。

## 11. 方案：MathLive `<math-field>` 作「第二编辑形态」（非替换）

调研结论：在「开源 + 真·渲染态编辑 + 自带移动端虚拟键盘 + 强 round-trip + 活跃维护」五项交集里，
**MathLive（v0.110/MIT/周下载 25 万）是当前唯一全中的开源项目**。MathQuill 矩阵 round-trip 十年
硬伤 + 无键盘；Tiptap 官方 Mathematics / prosemirror-math 只是"改裸源码 + KaTeX 预览"不算可视化；
MathType 闭源收费。

**工程量判断 = 刚刚好（第二形态，非替换）**：
- 整体替换 textarea = **过度工程**：主场景是编辑 OCR 已产出公式，可视化建公式需求弱；而 MathLive
  嵌 ProseMirror NodeView 有**地基级焦点摩擦**（第 14 节）+ gzip ~225KB + 「碰过即整段规范化」
  风险（第 13 节）。为弱需求引入这些代价不划算。
- 完全不做 = 欠工程（不懂 LaTeX 的人用不了）。
- 结论：双击默认进可视化 `<math-field>`，提供「切到源码」按钮回退现有 textarea；风险全关在单个
  节点编辑态内，可一键回退。

**分期（强制阶段化，逐期出证据）**：
| 阶段 | 范围 | 验收 |
|---|---|---|
| 3a 依赖+资源 | 装 `mathlive`、动态 `import()`、`soundsDirectory=null`、字体白嫖 katex CSS | Playwright：`MathfieldElement` 可构造/`getValue/setValue`；无 font/sound 404 |
| 3b NodeView 接 math-field + dirty 闸口 | 编辑态挂 `<math-field>`（只读态仍 KaTeX）；`input`-dirty 守门；`stopEvent`/`setContent` 守卫 | **先做 round-trip 真浏览器 spike**；Playwright 焦点/编辑/未敲键 0 腐蚀 |
| 3c 源码回退 + i18n + 样式 | math-field 内「切到源码」按钮回退 textarea；三语 + App.css | 视觉验证截图 |

## 12. NodeView 改造（mathNodes.ts）
只读态 KaTeX 渲染（`renderMath`）、序列化（`renderHTML`）、`data-latex` 唯一真相**全部不动**，
仅替换"进入编辑后挂什么"：`enterEdit()` 从 `document.createElement("textarea")` 改为动态
`import("mathlive")` 后 `new MathfieldElement()`：
- `mf.defaultMode = displayMode ? "math" : "inline-math"`；`mf.value = 原 latex`。
- 桌面强制虚拟键盘：`mathVirtualKeyboardPolicy="manual"` + `focusin→show()`/`focusout→hide()`。
- 提交走 `change` + `move-out`（光标移出边界 → `editor.view.focus()` 交还正文）；Esc 取消。
- `stopEvent` 改强为 `editing && mf.contains(target)`（仅 `return editing` 不够）；`ignoreMutation`
  保持 `true`；`destroy()` 补 `removeEventListener` + `mf.remove()`（StrictMode 防泄漏）。
- latex 输出用默认 `'latex'`（保留宏 + 享 verbatim 缓存），**不用** `'latex-expanded'`。

## 13. round-trip 保真闸口（核心，吸收对抗验证）
真 Chromium 实测：`setValue(原文)` 后**未编辑** `getValue('latex')` —— 21/21 逐字相等（含
`\operatorname`/`\mathbf`/`pmatrix`/`\\`）；**但一旦编辑（哪怕敲一字符再删）→ 整条公式 verbatim
缓存全丢、整段规范化重写**（`\boldsymbol→\bm`、矩阵空格塌），爆炸半径是**整公式 all-or-nothing**。

闸口规则：
1. **判据用 `input`-dirty 标志，绝不用"getValue 后与 originalLatex 字符串比较"**——用户碰了但
   语义没变时 getValue 已返回规范化串，字符串比较会误判"变了"而写回规范化版破坏保真。
   正确：`mf.addEventListener("input", () => dirty=true)`；`commit` 里 `if(!dirty){renderMath();return}`
   ——**未敲键连 getValue 都不调，原 `data-latex` 原封不动**。
2. dirty===true 才接受规范化（此时用户确实改了，规范化是其期望）。
3. 可接受性：用户编辑过的公式被规范化（语义等价、预览正确），未编辑的逐字保真；需逐字精确时
   走「切到源码」textarea 形态。

## 14. 风险与缓解（吸收 NodeView 焦点对抗验证）
ProseMirror 作者确认**架构级硬约束**：焦点不可共享，聚焦 math-field 必然 blur ProseMirror。故列
「第二形态」而非替换。
| # | 风险 | 缓解 |
|---|---|---|
| R1 | 聚焦 math-field → PM 失焦 → 工具栏/光标失真 | `stopEvent=editing&&contains`；`move-out`+方向键边界手动 `view.focus()` |
| R2 | **编辑中途外部 `setContent`（`MarkdownWysiwygEditor.tsx:351`，切 tab/保存回灌）→ NodeView 重建 → math-field 被 remove → 未提交内容静默丢失** | math-field 聚焦/编辑时跳过或延迟外部 value 同步 effect 的 `setContent`；或 destroy 前抢救性 commit |
| R3 | 行内 atom 选区（段首拖选删不掉）+ 嵌套 contenteditable 光标跳 | 论坛方案两侧塞无 src `<img>` 占位；或**块级先上 MathLive、行内暂留 textarea**（3b 实测定） |
| R4 | StrictMode 双挂载 + 全局键盘单例 → 监听重复/泄漏 | `destroy()` 内清监听；show/hide 幂等 |
| R5 | round-trip 碰过即规范化（第 13） | dirty 闸口 + textarea 源码逃生 |
| R6 | MathLive 0.x 序列化方言非稳定契约 | Playwright fixture 快照锁住，升级复跑 |
| R7 | 体积 gzip ~225KB | 动态 `await import("mathlive")`，仅进编辑时加载 |

**核心兜底**：所有冲突关在单个 math 节点编辑态内，异常可一键「切到源码」回退已验证零冲突的 textarea。

## 15. 测试策略与资源（吸收 jsdom + 资源对抗验证）
- **jsdom 测不了 MathLive**：`appendChild(<math-field>)` 触发 `connectedCallback` 连环撞缺失 API →
  **未捕获异常让 vitest 退出码=1 拖红 CI**，且 jsdom 下 `getValue` 退化成回吐缓存（"保真"是假象）。
  分层：① NodeView↔latex 回写逻辑（dirty 闸口/`setNodeMarkup`）→ vitest 把 `<math-field>` **mock 成
  轻量桩**，绝不真挂；② 渲染/光标/焦点/保真 → **一律 Playwright 真 Chromium**，fixture 断言"KaTeX
  可渲染/语义等价"不断言逐字相等。
- **现有测试兼容**：`mathRoundtrip.test.ts` 不受影响（走序列化路径）；`mathNodeView.test.ts` 需改写
  （裸 textarea 断言 → mock math-field 桩验 dirty 闸口，真渲染移 Playwright）。
- **资源（最低风险）**：项目已 `import "katex/dist/katex.min.css"`，其 12 个 `KaTeX_*` family 与
  MathLive 字体探测名单逐字一致 → 字体白嫖、**零打包增量**；`MathfieldElement.soundsDirectory=null`
  关音效；**严禁相对 `fontsDirectory`**（Vite hash 路径错位）。纯 CSR 无 SSR 问题。

## 16. 待用户确认
1. MathLive 作「第二编辑形态 + 保留 textarea 源码回退」（非整体替换）—— 认可？
2. 行内公式首期是否也上 MathLive，还是「块级先上、行内暂留 textarea」（R3 行内选区有已知 hack）？
3. 接受"编辑过的公式被 MathLive 规范化（语义等价/预览正确）、未编辑逐字保真"边界（第 13.3）？
4. 分期（3a/3b/3c）+ 3b 前先做 round-trip 真浏览器 spike —— 照此推进？
