/**
 * Tiptap 数学公式节点（方案 B：自定义节点 + KaTeX，与预览侧同栈）。
 *
 * 阶段 1（保真）：把 `$...$` / `$$...$$` 表示为 atom 节点，原始 LaTeX 存进
 * `data-latex` 属性（**唯一真相**），节点以纯文本 `$...$` 显示——本期不渲染，
 * 但 round-trip 中 marked / turndown 都不触碰 latex（它在属性里，不会被当
 * markdown 解析或转义），保证编辑器不会改坏公式。
 * 阶段 2 将在此基础上加 KaTeX NodeView + 双击编辑（见 editor-math-design.md）。
 */
import { Node as TiptapNode, mergeAttributes } from "@tiptap/core";

/** 占位元素属性名：markdownRoundtrip 与本模块共用同一份契约，避免漂移。 */
export const MATH_INLINE_DATA = "data-math-inline";
export const MATH_DISPLAY_DATA = "data-math-display";
export const MATH_LATEX_DATA = "data-latex";

/** 从节点属性安全取 latex 字符串。 */
function readLatex(attrs: { latex?: unknown }): string {
  return typeof attrs.latex === "string" ? attrs.latex : "";
}

/** 行内公式 `$...$`：inline atom，latex 存 data-latex，显示为源码文本。 */
export const MathInline = TiptapNode.create({
  name: "mathInline",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      latex: {
        default: "",
        parseHTML: (el: HTMLElement): string =>
          el.getAttribute(MATH_LATEX_DATA) ?? "",
        renderHTML: (attrs: { latex?: unknown }): Record<string, string> => ({
          [MATH_LATEX_DATA]: readLatex(attrs),
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: `span[${MATH_INLINE_DATA}]` }];
  },

  renderHTML({ HTMLAttributes }) {
    const latex = String(HTMLAttributes[MATH_LATEX_DATA] ?? "");
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        [MATH_INLINE_DATA]: "",
        class: "wysiwyg-math wysiwyg-math-inline",
      }),
      `$${latex}$`,
    ];
  },
});

/** 块级公式 `$$...$$`：block atom，latex 存 data-latex，显示为源码文本。 */
export const MathBlock = TiptapNode.create({
  name: "mathBlock",
  group: "block",
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      latex: {
        default: "",
        parseHTML: (el: HTMLElement): string =>
          el.getAttribute(MATH_LATEX_DATA) ?? "",
        renderHTML: (attrs: { latex?: unknown }): Record<string, string> => ({
          [MATH_LATEX_DATA]: readLatex(attrs),
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: `div[${MATH_DISPLAY_DATA}]` }];
  },

  renderHTML({ HTMLAttributes }) {
    const latex = String(HTMLAttributes[MATH_LATEX_DATA] ?? "");
    return [
      "div",
      mergeAttributes(HTMLAttributes, {
        [MATH_DISPLAY_DATA]: "",
        class: "wysiwyg-math wysiwyg-math-block",
      }),
      `$$${latex}$$`,
    ];
  },
});
