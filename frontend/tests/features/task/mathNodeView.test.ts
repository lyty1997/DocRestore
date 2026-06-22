/**
 * 编辑器公式 NodeView 测试：KaTeX 只读渲染 + 序列化保真 + 编辑回写 + dirty 闸口。
 *
 * 编辑界面默认是可视化 `<math-field>`（MathLive）。jsdom 测不了 MathLive（挂载会让
 * vitest 退出码=1，见 editor-math-design.md §15），故这里 mock 掉 mathlive 让编辑
 * **自动回退源码 textarea**，在源码路径上验证 NodeView↔latex 回写与 dirty 闸口逻辑；
 * 真实可视化渲染/光标/焦点/规范化走 Playwright。
 */

import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { afterEach, describe, expect, it, vi } from "vitest";

import { htmlToMarkdown, markdownToHtml } from "../../../src/features/task/markdownRoundtrip";
import {
  MathBlock,
  MathInline,
  insertMathNode,
} from "../../../src/features/task/mathNodes";

// MathLive 不可用 → NodeView 编辑回退源码 textarea（确定性、不加载真包、不崩 jsdom）。
vi.mock("mathlive", () => ({ MathfieldElement: undefined }));

let editor: Editor | undefined;

afterEach(() => {
  editor?.destroy();
  editor = undefined;
});

function mountEditor(md: string): Editor {
  editor = new Editor({
    extensions: [StarterKit, MathInline, MathBlock],
    content: markdownToHtml(md),
  });
  return editor;
}

/** 找到第一个指定类型节点的文档位置。 */
function findPos(ed: Editor, typeName: string): number | undefined {
  let pos: number | undefined;
  ed.state.doc.descendants((n, p) => {
    if (pos === undefined && n.type.name === typeName) pos = p;
    return pos === undefined;
  });
  return pos;
}

/** 等待编辑界面（回退后的源码框）出现——进入编辑是异步的（动态 import mathlive）。 */
async function waitForEl<T extends Element>(ed: Editor, sel: string): Promise<T> {
  return vi.waitFor(() => {
    const el = ed.view.dom.querySelector<T>(sel);
    if (el === null) throw new Error(`未出现: ${sel}`);
    return el;
  });
}

describe("公式 NodeView 渲染", () => {
  it("块级公式在编辑器内渲染为 KaTeX", () => {
    const ed = mountEditor("$$E=mc^2$$");
    expect(ed.view.dom.querySelector(".katex")).not.toBeNull();
    expect(ed.view.dom.querySelector(".katex-display")).not.toBeNull();
  });

  it("行内公式在编辑器内渲染为 KaTeX（非 display）", () => {
    const ed = mountEditor("前 $a+b$ 后");
    expect(ed.view.dom.querySelector(".katex")).not.toBeNull();
    expect(ed.view.dom.querySelector(".katex-display")).toBeNull();
  });

  it("坏公式不抛错（throwOnError:false）", () => {
    expect(() => mountEditor(String.raw`$$\frac{1}{$$`)).not.toThrow();
  });
});

describe("NodeView 不影响序列化（保真仍成立）", () => {
  it("getHTML 仍输出 data-latex，round-trip 逐字保留", () => {
    const ed = mountEditor("设 $x_0$ 收尾");
    expect(ed.getHTML()).toContain('data-latex="x_0"');
    expect(htmlToMarkdown(ed.getHTML())).toContain("$x_0$");
  });
});

describe("双击编辑（MathLive 回退源码）", () => {
  it("双击块级公式弹出 latex 源码框，载入原 latex", async () => {
    const ed = mountEditor("$$E=mc^2$$");
    ed.view.dom
      .querySelector(".wysiwyg-math-block")
      ?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    const input = await waitForEl<HTMLTextAreaElement>(
      ed, "textarea.wysiwyg-math-edit",
    );
    expect(input.value).toBe("E=mc^2");
  });

  it("编辑后回车提交：更新 latex 属性，序列化随之变化", async () => {
    const ed = mountEditor("$$E=mc^2$$");
    ed.view.dom
      .querySelector(".wysiwyg-math-block")
      ?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    const input = await waitForEl<HTMLTextAreaElement>(
      ed, "textarea.wysiwyg-math-edit",
    );
    input.value = "a^2+b^2";
    input.dispatchEvent(new Event("input", { bubbles: true })); // 置 dirty
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    const pos = findPos(ed, "mathBlock");
    if (pos === undefined) throw new Error("no mathBlock");
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("a^2+b^2");
    expect(htmlToMarkdown(ed.getHTML())).toContain("a^2+b^2");
  });

  it("dirty 闸口：改了 value 但未触发 input 事件 → 不写回（保原 latex）", async () => {
    const ed = mountEditor("$$E=mc^2$$");
    ed.view.dom
      .querySelector(".wysiwyg-math-block")
      ?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    const input = await waitForEl<HTMLTextAreaElement>(
      ed, "textarea.wysiwyg-math-edit",
    );
    input.value = "HACKED"; // 直接改但不派发 input → dirty 仍 false
    input.dispatchEvent(new FocusEvent("blur"));
    const pos = findPos(ed, "mathBlock");
    if (pos === undefined) throw new Error("no mathBlock");
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("E=mc^2"); // 0 腐蚀
  });

  it("Esc 取消：丢弃改动、还原原 latex", async () => {
    const ed = mountEditor("$$E=mc^2$$");
    ed.view.dom
      .querySelector(".wysiwyg-math-block")
      ?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    const input = await waitForEl<HTMLTextAreaElement>(
      ed, "textarea.wysiwyg-math-edit",
    );
    input.value = "changed";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    const pos = findPos(ed, "mathBlock");
    if (pos === undefined) throw new Error("no mathBlock");
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("E=mc^2");
  });
});

describe("插入公式（工具栏）", () => {
  it("插入块级公式：建空 mathBlock 节点并自动进入编辑", async () => {
    const ed = mountEditor("正文");
    insertMathNode(ed, true);
    const pos = findPos(ed, "mathBlock");
    if (pos === undefined) throw new Error("no mathBlock");
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("");
    // 空公式自动进入编辑 → 源码框出现，填入并回车
    const input = await waitForEl<HTMLTextAreaElement>(
      ed, "textarea.wysiwyg-math-edit",
    );
    input.value = "x^2";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("x^2");
    expect(htmlToMarkdown(ed.getHTML())).toContain("x^2");
  });

  it("插入行内公式：建空 mathInline 节点并自动进入编辑", async () => {
    const ed = mountEditor("前后");
    insertMathNode(ed, false);
    const pos = findPos(ed, "mathInline");
    if (pos === undefined) throw new Error("no mathInline");
    expect(ed.state.doc.nodeAt(pos)?.type.name).toBe("mathInline");
    const input = await waitForEl<HTMLInputElement>(ed, "input.wysiwyg-math-edit");
    expect(input).not.toBeNull();
  });
});
