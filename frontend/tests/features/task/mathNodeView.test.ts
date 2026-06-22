/**
 * 编辑器公式 NodeView 测试（阶段 2）：KaTeX 渲染 + 双击编辑 +
 * 序列化（getHTML / round-trip）不受 NodeView 影响（保真仍成立）。
 */

import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { afterEach, describe, expect, it } from "vitest";

import { htmlToMarkdown, markdownToHtml } from "../../../src/features/task/markdownRoundtrip";
import {
  MathBlock,
  MathInline,
  insertMathNode,
} from "../../../src/features/task/mathNodes";

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

describe("公式 NodeView 渲染", () => {
  it("块级公式在编辑器内渲染为 KaTeX", () => {
    const ed = mountEditor("$$E=mc^2$$");
    const katexEl = ed.view.dom.querySelector(".katex");
    expect(katexEl).not.toBeNull();
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

describe("双击编辑公式", () => {
  it("双击块级公式弹出 latex 输入框", () => {
    const ed = mountEditor("$$E=mc^2$$");
    const mathEl = ed.view.dom.querySelector(".wysiwyg-math-block");
    expect(mathEl).not.toBeNull();
    mathEl?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    const input = ed.view.dom.querySelector<HTMLTextAreaElement>(
      "textarea.wysiwyg-math-edit",
    );
    expect(input).not.toBeNull();
    expect(input?.value).toBe("E=mc^2");
  });

  it("编辑后回车提交：更新 latex 属性并重渲染，序列化随之变化", () => {
    const ed = mountEditor("$$E=mc^2$$");
    const mathEl = ed.view.dom.querySelector(".wysiwyg-math-block");
    mathEl?.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    const input = ed.view.dom.querySelector<HTMLTextAreaElement>(
      "textarea.wysiwyg-math-edit",
    );
    expect(input).not.toBeNull();
    if (input === null) return;
    input.value = "a^2+b^2";
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
    // 节点属性更新
    const pos = findPos(ed, "mathBlock");
    expect(pos).not.toBeUndefined();
    if (pos === undefined) return;
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("a^2+b^2");
    // 序列化反映新 latex
    expect(ed.getHTML()).toContain('data-latex="a^2+b^2"');
    expect(htmlToMarkdown(ed.getHTML())).toContain("a^2+b^2");
  });
});

describe("插入公式（工具栏）", () => {
  it("插入块级公式：建空 mathBlock 节点并自动进入编辑", () => {
    const ed = mountEditor("正文");
    insertMathNode(ed, true);
    // 节点已插入
    const pos = findPos(ed, "mathBlock");
    expect(pos).not.toBeUndefined();
    if (pos === undefined) return;
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("");
    // 空公式被选中 → selectNode 自动进入编辑 → 出现 latex 输入框
    const input = ed.view.dom.querySelector<HTMLTextAreaElement>(
      "textarea.wysiwyg-math-edit",
    );
    expect(input).not.toBeNull();
    // 填入并回车提交，序列化为 $$..$$
    if (input === null) return;
    input.value = "x^2";
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
    expect(ed.state.doc.nodeAt(pos)?.attrs.latex).toBe("x^2");
    expect(htmlToMarkdown(ed.getHTML())).toContain("x^2");
  });

  it("插入行内公式：建空 mathInline 节点", () => {
    const ed = mountEditor("前后");
    insertMathNode(ed, false);
    const pos = findPos(ed, "mathInline");
    expect(pos).not.toBeUndefined();
    if (pos === undefined) return;
    expect(ed.state.doc.nodeAt(pos)?.type.name).toBe("mathInline");
    // 行内空公式同样自动进入编辑（input 元素）
    const input = ed.view.dom.querySelector<HTMLInputElement>(
      "input.wysiwyg-math-edit",
    );
    expect(input).not.toBeNull();
  });
});
