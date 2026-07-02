/**
 * 编辑器光标块检测 ``blockAtCursor``（Epic E · E3）：挂真实 Tiptap Editor（jsdom），
 * 把光标落进不同段落，断言返回「最近前置页 + 该段文字」，且页随光标跨页更新。
 */

import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { afterEach, describe, expect, it } from "vitest";

import {
  PageAnchor,
  blockAtCursor,
} from "../../src/components/MarkdownWysiwygEditor";

let editor: Editor | undefined;

afterEach(() => {
  editor?.destroy();
  editor = undefined;
});

function mount(html: string): Editor {
  editor = new Editor({ extensions: [StarterKit, PageAnchor], content: html });
  return editor;
}

/** 把光标落进首个 textContent 匹配的段落内部，返回该位置。 */
function selectParagraph(ed: Editor, text: string): void {
  let pos: number | undefined;
  ed.state.doc.descendants((node, p) => {
    if (
      pos === undefined &&
      node.type.name === "paragraph" &&
      node.textContent === text
    ) {
      pos = p + 1; // +1 进入段落内部
    }
    return pos === undefined;
  });
  if (pos === undefined) throw new Error(`未找到段落: ${text}`);
  ed.commands.setTextSelection(pos);
}

const CONTENT =
  '<div data-page-anchor data-page="IMG_0001.jpg"></div>' +
  "<p>第一页的正文段落</p>" +
  '<div data-page-anchor data-page="IMG_0002.jpg"></div>' +
  "<p>第二页的正文段落</p>";

describe("blockAtCursor", () => {
  it("光标在第一段 → 第一页 + 该段文字", () => {
    const ed = mount(CONTENT);
    selectParagraph(ed, "第一页的正文段落");
    expect(blockAtCursor(ed)).toEqual({
      page: "IMG_0001.jpg",
      text: "第一页的正文段落",
    });
  });

  it("光标移到第二段 → 第二页 + 第二段文字（页随光标更新）", () => {
    const ed = mount(CONTENT);
    selectParagraph(ed, "第二页的正文段落");
    expect(blockAtCursor(ed)).toEqual({
      page: "IMG_0002.jpg",
      text: "第二页的正文段落",
    });
  });

  it("光标在首个页标记之前（无前置页）→ undefined", () => {
    const ed = mount(
      "<p>无页标记的段落</p>" +
        '<div data-page-anchor data-page="X.jpg"></div>',
    );
    selectParagraph(ed, "无页标记的段落");
    expect(blockAtCursor(ed)).toBeUndefined();
  });
});
