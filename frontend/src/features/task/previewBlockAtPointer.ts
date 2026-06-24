/**
 * 只读预览侧「指针所在块」求解（Epic E · E4，#88）。
 *
 * 预览模式没有 Tiptap 节点，改从渲染后的 DOM 求光标块，语义与编辑器
 * ``blockAtCursor``（MarkdownWysiwygEditor.tsx）一一对应：
 *   - 「块」= 命中元素向上到**容器直接子节点**（对应编辑器 ``$from.node(1)``，
 *     即 doc 的直接子块；react-markdown v9 把块直接渲染为容器直接子节点，
 *     列表/表格作为整块，与编辑器 depth-1 一致 → 匹配面与 E3 完全相同）；
 *   - 文本 = ``block.textContent``；
 *   - 页 = 该块**最近前置** ``[data-page]`` 锚点（``injectPageAnchors`` 已把
 *     ``<!-- page: X -->`` 转成 ``<span class="page-anchor" data-page="…">``）。
 * 容器外 / 空块 / 无前置页 → undefined（与编辑器侧同样退化为不高亮）。
 */

import type { CursorBlock } from "./blockHighlight";

/**
 * 命中元素向上取容器的直接子块；命中容器自身 / 不在容器内 → undefined。
 *
 * ``container.contains`` 保证 target 是 container 后代，故向上必能走到
 * ``parentElement === container`` 终止（``=== null`` 仅作类型兜底，不会发生）。
 */
function topLevelBlockOf(
  target: Element,
  container: HTMLElement,
): Element | undefined {
  if (target === container) return undefined;
  if (!container.contains(target)) return undefined;
  let node: Element = target;
  while (node.parentElement !== container) {
    if (node.parentElement === null) return undefined;
    node = node.parentElement;
  }
  return node;
}

/**
 * 文档序里该块**之前**最后一个 ``[data-page]`` 锚点的页键。
 *
 * ``querySelectorAll`` 按文档序返回；一旦遇到不在块之前的锚点（块自身或其后），
 * 后续也都在其后，直接停。块内部的锚点（空锚点块自身）判定为非前置 → 跳过。
 */
function nearestPrecedingPage(
  block: Element,
  container: HTMLElement,
): string | undefined {
  const anchors = container.querySelectorAll<HTMLElement>("[data-page]");
  let page: string | undefined;
  for (const anchor of anchors) {
    const relation = block.compareDocumentPosition(anchor);
    if ((relation & Node.DOCUMENT_POSITION_PRECEDING) === 0) break;
    const key = anchor.dataset.page;
    if (key !== undefined && key !== "") page = key;
  }
  return page;
}

/**
 * 预览 DOM 里指针所在块 → 光标块（页 + 纯文本），供模糊匹配版面块。
 *
 * @param target 指针事件命中的元素（``e.target``，可能为 null/undefined）。
 * @param container 预览滚动容器（``.markdown-preview``，即 ``e.currentTarget``）。
 */
export function previewBlockAtPointer(
  target: Element | null | undefined,
  container: HTMLElement,
): CursorBlock | undefined {
  if (target === null || target === undefined) return undefined;
  const block = topLevelBlockOf(target, container);
  if (block === undefined) return undefined;
  const text = block.textContent.trim();
  if (text === "") return undefined;
  const page = nearestPrecedingPage(block, container);
  if (page === undefined) return undefined;
  return { page, text };
}
