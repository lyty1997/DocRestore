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
  type Editor,
  type NodeViewRendererProps,
} from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { NodeSelection } from "@tiptap/pm/state";
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

/** 当前正处于编辑态的公式节点计数（R2：编辑器组件据此跳过外部 setContent 回灌）。 */
let _mathEditCount = 0;

/**
 * 是否有公式节点正在编辑。编辑器组件在外部 value 变化时若此为真，应跳过
 * `setContent`——否则 NodeView 重建会销毁正在编辑的 math-field，未提交内容丢失。
 */
export function isMathEditing(): boolean {
  return _mathEditCount > 0;
}

/** MathLive 构造器 / 实例类型（仅作类型，inline import 在运行时被擦除）。 */
type MathfieldCtor = typeof import("mathlive").MathfieldElement;
type MathfieldEl = InstanceType<MathfieldCtor>;

/** 缓存的 MathLive 构造器加载 promise（全局仅加载一次）。 */
let _mfCtorPromise: Promise<MathfieldCtor | undefined> | undefined;

/**
 * 按需动态加载 MathLive（~225KB，仅用户进入可视化编辑时拉取，不进首屏）。
 * 返回 MathfieldElement 构造器；加载失败 / 测试环境（SSR 包无此导出）返回
 * undefined，调用方据此回退源码 textarea。
 */
async function ensureMathfieldCtor(): Promise<MathfieldCtor | undefined> {
  _mfCtorPromise ??= import("mathlive").then(
    (m): MathfieldCtor | undefined => {
      // 运行时类型撑宽：SSR 包 / 加载异常时 MathfieldElement 实为 undefined。
      const ctor = m.MathfieldElement as MathfieldCtor | undefined;
      if (ctor !== undefined) {
        // 关音效：MathLive API 要求 null 显式禁用（字体白嫖 katex CSS，免 fontsDirectory）。
        // eslint-disable-next-line unicorn/no-null
        ctor.soundsDirectory = null;
      }
      return ctor;
    },
    (): undefined => undefined,
  );
  return _mfCtorPromise;
}

/** MathLive 全局虚拟键盘单例（运行时由 mathlive 挂到全局，此处局部声明类型）。 */
function getMathKeyboard(): { show: () => void; hide: () => void } | undefined {
  return (
    globalThis as unknown as {
      mathVirtualKeyboard?: { show: () => void; hide: () => void };
    }
  ).mathVirtualKeyboard;
}

/** 桌面也弹虚拟键盘（不懂 LaTeX 用户必需；触屏默认自动，桌面手动控制）。 */
function showMathKeyboard(): void {
  getMathKeyboard()?.show();
}
function hideMathKeyboard(): void {
  getMathKeyboard()?.hide();
}

/**
 * 公式节点 NodeView 工厂：只读态 KaTeX 渲染；双击进入编辑。
 *
 * 编辑界面双形态：默认**可视化** `<math-field>`（不懂 LaTeX 也能点虚拟键盘建公式），
 * 「切到源码」回退 **textarea** 改 LaTeX；MathLive 加载失败自动回退源码。
 * round-trip 闸口：`dirty` 标志挂 math-field 的 `input` —— 未敲键不取 `getValue`、
 * 原 `data-latex` 原封不动（MathLive 碰过即整段规范化，未碰逐字保真）。
 *
 * @param displayMode true=块级（div, displaystyle）/ false=行内（span, inline）
 */
function createMathNodeView(
  displayMode: boolean,
  labels: { source: string; visual: string },
): (props: NodeViewRendererProps) => NodeView {
  return ({ node, editor, getPos }: NodeViewRendererProps): NodeView => {
    let current: PMNode = node;
    let editing = false;
    /** 本次编辑会话开始时的 latex（未改动时按此 0 腐蚀还原）。 */
    let sessionLatex = "";
    /** 用户是否真正改动过（math-field input / textarea 输入）。 */
    let dirty = false;
    /** 拆除当前编辑界面（math-field 或 textarea）的回调。 */
    let teardown: (() => void) | undefined;

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
      katex.render(latex, dom, { displayMode, throwOnError: false, strict: false });
    };

    /** 退出编辑：拆界面；仅当 dirty 且变化才写回（0 腐蚀）。save=false 始终丢弃。 */
    const finishEdit = (latex: string, save: boolean): void => {
      if (!editing) return;
      editing = false;
      _mathEditCount -= 1;
      teardown?.();
      teardown = undefined;
      const pos = getPos();
      if (save && dirty && typeof pos === "number" && latex !== current.attrs.latex) {
        editor.view.dispatch(
          editor.view.state.tr.setNodeMarkup(pos, undefined, {
            ...current.attrs,
            latex,
          }),
        );
        return; // update() 随 dispatch 触发并 renderMath
      }
      renderMath(); // 取消 / 未改 / 无 pos：还原（原 latex 不动）
    };

    /** dirty 标志：用户真正改动（math-field input / textarea 输入）才置位。 */
    const markDirty = (): void => {
      dirty = true;
    };
    /** 可视化态 Esc 取消：丢弃改动、按 sessionLatex 还原。 */
    const onVisualEscape = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.preventDefault();
        finishEdit(sessionLatex, false);
      }
    };

    /** 「切到源码 / 可视化」小按钮（mousedown preventDefault 防失焦提前提交）。 */
    const makeToggle = (
      symbol: string,
      title: string,
      onToggle: () => void,
    ): HTMLButtonElement => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "wysiwyg-math-mode-toggle";
      b.textContent = symbol;
      b.title = title;
      b.addEventListener("mousedown", (e): void => {
        e.preventDefault();
      });
      b.addEventListener("click", (e): void => {
        e.preventDefault();
        onToggle();
      });
      return b;
    };

    /** 源码 textarea 编辑界面。 */
    function mountSource(latex: string): void {
      const input = document.createElement(displayMode ? "textarea" : "input");
      input.value = latex;
      input.className = "wysiwyg-math-edit";
      if (input instanceof HTMLTextAreaElement) input.rows = 2;
      const inputEl: HTMLElement = input;
      const onBlur = (): void => {
        finishEdit(input.value, true);
      };
      const onKeydown = (e: KeyboardEvent): void => {
        if (e.key === "Escape") {
          e.preventDefault();
          finishEdit(input.value, false);
        } else if (e.key === "Enter" && !(displayMode && e.shiftKey)) {
          e.preventDefault();
          finishEdit(input.value, true);
        }
      };
      inputEl.addEventListener("input", markDirty);
      inputEl.addEventListener("blur", onBlur);
      inputEl.addEventListener("keydown", onKeydown);
      const detach = (): void => {
        inputEl.removeEventListener("input", markDirty);
        inputEl.removeEventListener("blur", onBlur);
        inputEl.removeEventListener("keydown", onKeydown);
      };
      const toggle = makeToggle("∑", labels.visual, (): void => {
        const v = input.value;
        detach(); // 防拆除期间 blur 触发提交
        teardown = undefined;
        void mountVisual(v);
      });
      const wrap = document.createElement(displayMode ? "div" : "span");
      wrap.className = "wysiwyg-math-editwrap";
      wrap.append(input, toggle);
      dom.replaceChildren(wrap);
      input.focus();
      teardown = detach;
    }

    /** 可视化 math-field 编辑界面（MathLive 加载失败回退源码）。 */
    async function mountVisual(latex: string): Promise<void> {
      const ctor = await ensureMathfieldCtor();
      if (!editing) return; // 加载期间已退出
      if (ctor === undefined) {
        mountSource(latex); // 无 MathLive → 回退源码
        return;
      }
      const mf: MathfieldEl = new ctor();
      mf.value = latex;
      mf.defaultMode = displayMode ? "math" : "inline-math";
      mf.mathVirtualKeyboardPolicy = "manual";
      // dirty 闸口：未敲键不取 getValue（避免规范化污染），按 sessionLatex 原样还原。
      const valueNow = (): string => (dirty ? mf.getValue("latex") : sessionLatex);
      const onChange = (): void => {
        finishEdit(valueNow(), true);
      };
      const onMoveOut = (): void => {
        finishEdit(valueNow(), true);
        editor.view.focus(); // 光标移出公式 → 焦点交还正文
      };
      mf.addEventListener("input", markDirty);
      mf.addEventListener("change", onChange);
      mf.addEventListener("move-out", onMoveOut);
      mf.addEventListener("focusin", showMathKeyboard);
      mf.addEventListener("focusout", hideMathKeyboard);
      mf.addEventListener("keydown", onVisualEscape);
      const detach = (): void => {
        hideMathKeyboard();
        mf.removeEventListener("input", markDirty);
        mf.removeEventListener("change", onChange);
        mf.removeEventListener("move-out", onMoveOut);
        mf.removeEventListener("focusin", showMathKeyboard);
        mf.removeEventListener("focusout", hideMathKeyboard);
        mf.removeEventListener("keydown", onVisualEscape);
        mf.remove();
      };
      const toggle = makeToggle("</>", labels.source, (): void => {
        const v = valueNow();
        detach();
        teardown = undefined;
        mountSource(v);
      });
      const wrap = document.createElement(displayMode ? "div" : "span");
      wrap.className = "wysiwyg-math-editwrap";
      wrap.append(mf, toggle);
      dom.replaceChildren(wrap);
      mf.focus();
      teardown = detach;
    }

    /** 进入编辑：默认可视化（不懂 LaTeX 友好），加载失败回退源码。 */
    const enterEdit = (): void => {
      if (editing || !editor.isEditable) return;
      editing = true;
      _mathEditCount += 1;
      dirty = false;
      sessionLatex = readLatex(current.attrs);
      void mountVisual(sessionLatex);
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
        // 空公式被选中（如「插入公式」刚插入或单击占位）→ 自动进入编辑，
        // 免去再双击；非空公式仅高亮（双击才编辑）。
        if (!editing && readLatex(current.attrs) === "") {
          enterEdit();
        }
      },
      deselectNode: (): void => {
        dom.classList.remove("wysiwyg-math-selected");
      },
      // 编辑中拦下编辑界面内的事件，不让 ProseMirror 接管键盘/指针/选区。
      stopEvent: (e: Event): boolean =>
        editing && e.target instanceof Node && dom.contains(e.target),
      ignoreMutation: (): boolean => true,
      destroy: (): void => {
        if (editing) {
          editing = false;
          _mathEditCount -= 1; // 编辑中被销毁（如外部 setContent）→ 计数归还
        }
        teardown?.();
        teardown = undefined;
      },
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

/** 公式节点扩展 options：编辑界面「切换源码/可视化」按钮 title（i18n 由组件注入）。 */
export interface MathNodeOptions {
  /** 「切到源码」按钮 title。 */
  sourceLabel: string;
  /** 「切到可视化」按钮 title。 */
  visualLabel: string;
}

const DEFAULT_MATH_OPTIONS: MathNodeOptions = {
  sourceLabel: "Edit LaTeX source",
  visualLabel: "Visual editor",
};

/** 行内公式 `$...$`：inline atom + KaTeX NodeView。 */
export const MathInline = TiptapNode.create<MathNodeOptions>({
  name: "mathInline",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,

  addOptions() {
    return { ...DEFAULT_MATH_OPTIONS };
  },

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
    return createMathNodeView(false, {
      source: this.options.sourceLabel,
      visual: this.options.visualLabel,
    });
  },
});

/** 块级公式 `$$...$$`：block atom + KaTeX NodeView。 */
export const MathBlock = TiptapNode.create<MathNodeOptions>({
  name: "mathBlock",
  group: "block",
  atom: true,
  selectable: true,

  addOptions() {
    return { ...DEFAULT_MATH_OPTIONS };
  },

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
    return createMathNodeView(true, {
      source: this.options.sourceLabel,
      visual: this.options.visualLabel,
    });
  },
});

/**
 * 工具栏「插入公式」：插入一个空 math 节点（行内 / 块级），并把选区落到该
 * 节点上 —— 触发 NodeView.selectNode → 空公式自动进入编辑（见 selectNode）。
 *
 * @param editor      Tiptap 编辑器
 * @param displayMode true=块级 `$$..$$` / false=行内 `$..$`
 */
export function insertMathNode(editor: Editor, displayMode: boolean): void {
  const type = displayMode ? "mathBlock" : "mathInline";
  editor
    .chain()
    .focus()
    .insertContent({ type, attrs: { latex: "" } })
    .command(({ tr, dispatch }): boolean => {
      // 选中刚插入的空公式节点 → 触发 NodeView.selectNode → 空公式自动进入编辑。
      // 块级插入会拆段、节点位置不易由选区反推，故直接扫描「最后一个同类空节点」
      //（本函数仅工具栏调用、插入后立即编辑，最新插入即文档序最后一个空节点）。
      if (dispatch) {
        let target: number | undefined;
        tr.doc.descendants((n, p): boolean => {
          if (n.type.name === type && readLatex(n.attrs) === "") target = p;
          return true;
        });
        if (target !== undefined) {
          tr.setSelection(NodeSelection.create(tr.doc, target));
        }
      }
      return true;
    })
    .run();
}
