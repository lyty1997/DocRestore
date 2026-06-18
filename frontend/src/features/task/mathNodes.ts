/**
 * Tiptap 数学公式节点（方案 B：自定义节点 + KaTeX，与预览侧同栈）。
 *
 * - 阶段 1（保真）：`$...$` / `$$...$$` 表示为 atom 节点，原始 LaTeX 存
 *   `data-latex`（**唯一真相**）；`renderHTML`（= 序列化 / getHTML）始终输出
 *   `data-latex`，marked / turndown 都不触碰 latex，保证 round-trip 逐字不变。
 * - 阶段 2（渲染 + 交互）：`addNodeView` 用 KaTeX 把 latex 渲染成数学，双击进入
 *   编辑（textarea/input 改 data-latex，失焦 / 回车提交、Esc 取消）。NodeView 只影响
 *   **编辑态显示**，不参与序列化——故阶段 1 的保真不受影响。
 *
 * 注：KaTeX 的 CSS 由编辑器组件 `MarkdownWysiwygEditor` 引入（避免进入单测导入图）。
 */
import {
  Node as TiptapNode,
  mergeAttributes,
  type NodeViewRendererProps,
} from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import type { NodeView } from "@tiptap/pm/view";
import katex from "katex";

/** 占位元素属性名：markdownRoundtrip 与本模块共用同一份契约，避免漂移。 */
export const MATH_INLINE_DATA = "data-math-inline";
export const MATH_DISPLAY_DATA = "data-math-display";
export const MATH_LATEX_DATA = "data-latex";

/** 从节点属性安全取 latex 字符串。 */
function readLatex(attrs: { latex?: unknown }): string {
  return typeof attrs.latex === "string" ? attrs.latex : "";
}

/**
 * 公式节点 NodeView 工厂：KaTeX 渲染 + 双击编辑。
 *
 * @param displayMode true=块级（div, KaTeX displayMode）/ false=行内（span）
 */
function createMathNodeView(
  displayMode: boolean,
): (props: NodeViewRendererProps) => NodeView {
  return ({ node, editor, getPos }: NodeViewRendererProps): NodeView => {
    let current: PMNode = node;
    let editing = false;

    const dom: HTMLElement = document.createElement(displayMode ? "div" : "span");
    dom.className = displayMode
      ? "wysiwyg-math wysiwyg-math-block"
      : "wysiwyg-math wysiwyg-math-inline";
    dom.setAttribute(displayMode ? MATH_DISPLAY_DATA : MATH_INLINE_DATA, "");
    dom.contentEditable = "false";
    dom.title = "双击编辑公式";

    const renderMath = (): void => {
      const latex = readLatex(current.attrs);
      dom.setAttribute(MATH_LATEX_DATA, latex);
      if (latex.trim() === "") {
        dom.textContent = "（空公式，双击编辑）";
        return;
      }
      // KaTeX trust:false（默认）输出无 XSS；坏公式 throwOnError:false 渲红字不崩。
      katex.render(latex, dom, {
        displayMode,
        throwOnError: false,
        strict: false,
      });
    };

    const commit = (
      input: HTMLInputElement | HTMLTextAreaElement,
      save: boolean,
    ): void => {
      if (!editing) return;
      editing = false;
      const pos = getPos();
      if (save && typeof pos === "number") {
        editor.view.dispatch(
          editor.view.state.tr.setNodeMarkup(pos, undefined, {
            ...current.attrs,
            latex: input.value,
          }),
        );
        return; // update() 随 dispatch 触发并 renderMath
      }
      renderMath(); // 取消 / 无 pos：还原
    };

    const enterEdit = (): void => {
      if (editing || !editor.isEditable) return;
      editing = true;
      const input = document.createElement(displayMode ? "textarea" : "input");
      input.value = readLatex(current.attrs);
      input.className = "wysiwyg-math-edit";
      if (input instanceof HTMLTextAreaElement) input.rows = 2;
      dom.replaceChildren(input);
      input.focus();
      // inputEl 拓宽为 HTMLElement：联合元素类型（input|textarea）上
      // addEventListener 会丢失带类型事件重载，经 HTMLElement 引用调用即可。
      const inputEl: HTMLElement = input;
      inputEl.addEventListener("blur", (): void => {
        commit(input, true);
      });
      inputEl.addEventListener("keydown", (e: KeyboardEvent): void => {
        if (e.key === "Escape") {
          e.preventDefault();
          commit(input, false);
        } else if (e.key === "Enter" && !(displayMode && e.shiftKey)) {
          // 行内：回车提交；块级：回车提交、Shift+回车换行
          e.preventDefault();
          commit(input, true);
        }
      });
    };

    dom.addEventListener("dblclick", (e: MouseEvent): void => {
      e.preventDefault();
      enterEdit();
    });

    renderMath();

    return {
      dom,
      update: (updated: PMNode): boolean => {
        if (updated.type !== current.type) return false;
        current = updated;
        if (!editing) renderMath();
        return true;
      },
      selectNode: (): void => {
        dom.classList.add("wysiwyg-math-selected");
      },
      deselectNode: (): void => {
        dom.classList.remove("wysiwyg-math-selected");
      },
      stopEvent: (): boolean => editing,
      ignoreMutation: (): boolean => true,
    };
  };
}

/** 公共属性定义：latex ↔ data-latex。 */
function latexAttribute(): {
  latex: {
    default: string;
    parseHTML: (el: HTMLElement) => string;
    renderHTML: (attrs: { latex?: unknown }) => Record<string, string>;
  };
} {
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
}

/** 行内公式 `$...$`：inline atom + KaTeX NodeView。 */
export const MathInline = TiptapNode.create({
  name: "mathInline",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return latexAttribute();
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

  addNodeView() {
    return createMathNodeView(false);
  },
});

/** 块级公式 `$$...$$`：block atom + KaTeX NodeView。 */
export const MathBlock = TiptapNode.create({
  name: "mathBlock",
  group: "block",
  atom: true,
  selectable: true,

  addAttributes() {
    return latexAttribute();
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

  addNodeView() {
    return createMathNodeView(true);
  },
});
