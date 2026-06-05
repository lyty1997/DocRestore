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

# 代码模式预览加载性能优化设计

## 一、问题与根因

代码模式打开上千行源文件需要好几秒。瓶颈不在 `getCodeFileContent`（纯
`fetch().text()`，本地文件毫秒级），而在 `CodeViewer` 只读视图的渲染：

- 整文件一次性渲染、无虚拟化；
- `tokenizeCodeLine` 把每个语法 token 切成独立 `<span>`，一行十几个 token
  → 每行 ≈ 15 个 DOM 节点 → 1000 行 ≈ 1.5w 节点，一次同步 commit 让浏览器
  layout/paint，是"好几秒"的主因；
- `tokenizeCodeLine` 写在 render 里、无 memo，挂载期多次重渲染 → 整文件重复分词；
- `diagnosticItemsForLine` 每行 O(D) 全量 filter，`visibleDiagnostics` 在只读
  模式也照算（实际只在编辑面板用）→ O(N·D) 空转。

## 二、修复（四项，按性价比）

| 代号 | 措施 | 解决的开销 |
|---|---|---|
| A | `useMemo` 缓存整文件 token（key=content+language+path） | 重渲染重复分词 |
| B | 只读模式不算 `visibleDiagnostics`（仅编辑面板需要） | O(N·D) 空转 |
| C | `.code-line` 加 `content-visibility:auto` + `contain-intrinsic-size` | 视口外行 layout/paint |
| D | 行级虚拟化（固定行高 + 上下 spacer，只渲染可视窗口） | React 创建/reconcile 上万节点 |

A+B+C 无布局变更、低风险（Phase 1）；D 改渲染结构（Phase 2，单独提交）。

## 三、D 的关键设计：spacer 窗口 + 锚点 overlay

难点是虚拟化与 `useScrollSync` 的 `[data-page]` 锚点配合：锚点必须始终在 DOM
里且位置正确，但虚拟化会移除视口外的行。

方案——**行用 spacer 窗口（保持正常流布局），锚点用绝对定位 overlay 独立渲染**：

```
<div ref=scrollEl class="code-content-text">      // overflow:auto
  <div class="code-virtual-inner" position:relative; height = N*rowH>
    {锚点 overlay：所有 codePageAnchors，position:absolute, top=lineIndex*rowH}
    <div height = startIndex*rowH />               // 上 spacer
    {可视窗口行 [startIndex, endIndex)：正常流，grid/white-space:pre 不变}
    <div height = (N-endIndex)*rowH />             // 下 spacer
  </div>
</div>
```

要点：
- **固定行高**：源码行单行不换行（`white-space:pre`），行高均匀；`rowH` 挂载后
  从首个 `.code-line` 实测，默认估值 `0.85rem×1.55≈21px`，实测纠偏。
- **锚点与行同源**：都在 `code-virtual-inner` 内，共享同一 `padding-top` 原点，
  锚点 `top=lineIndex*rowH` 与对应行顶对齐；overlay 绝对定位不影响行流与横向滚动。
- **行仍走正常流**：可视行的 grid 布局、`min-width:max-content` 横向滚动行为
  与改造前一致（横向滚动范围随窗口取当前可视最宽行，可接受）。
- **窗口计算**：`start=floor(scrollTop/rowH)-overscan`、
  `end=ceil((scrollTop+viewportH)/rowH)+overscan`，overscan≈8 防快滚白屏。
- 滚动监听 rAF 节流写 `scrollTop` state；`ResizeObserver` 跟 `viewportH`；
  切文件（selectedPath 变）重置 scrollTop=0。

`getAnchorMetrics` 读锚点 `getBoundingClientRect` 仍成立 → 同步滚动不变。

## 四、工程量判断

- A/B/C：刚刚好（小改、消除明确浪费）。
- D：必要——A+B+C 砍了 layout/paint 与重复计算，但 React 创建上万节点的成本只有
  真窗口化能根治；超大文件下这是"根治"而非过度工程。代价是渲染结构复杂化，故
  限定固定行高 + spacer 最小侵入实现，并保留 A+B+C 独立提交以便回退。

## 五、验证

- 单测：窗口计算（给定 rowH/scrollTop/viewportH → [start,end] 正确）。
- 视觉：需用真实代码模式任务（含上千行源文件）人工核对——加载即时、滚动跟手、
  同步滚动与诊断高亮/行号不串位。无代码模式测试数据时该项由用户复核。
