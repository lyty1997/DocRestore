/**
 * Markdown ⇄ HTML round-trip 单测：保证 Tiptap WYSIWYG 编辑器在 load 与
 * save 之间不丢失语义关键内容（标题 / 列表 / 表格 / page anchor）。
 *
 * round-trip 不要求 byte-equal —— turndown 会重排空白、引号风格等；
 * 只断言再次 ``markdownToHtml`` 渲染后含相同结构信号。
 */

import { describe, expect, it } from "vitest";

import {
  htmlToMarkdown,
  markdownToHtml,
} from "../src/features/task/markdownRoundtrip";


describe("markdownRoundtrip", () => {
  describe("markdownToHtml", () => {
    it("renders headings", () => {
      const html = markdownToHtml("# Title\n\n## Sub");
      expect(html).toContain("<h1>");
      expect(html).toContain("Title");
      expect(html).toContain("<h2>");
    });

    it("renders GFM table", () => {
      const md = "| a | b |\n|---|---|\n| 1 | 2 |\n";
      const html = markdownToHtml(md);
      expect(html).toContain("<table");
      expect(html).toContain("<th>");
      expect(html).toContain("<td>");
    });

    it("renders lists", () => {
      const html = markdownToHtml("- one\n- two\n\n1. a\n2. b\n");
      expect(html).toContain("<ul>");
      expect(html).toContain("<ol>");
    });

    it("converts page anchor comment to data-page-anchor div", () => {
      const html = markdownToHtml("Hello\n\n<!-- page: DSC0001.JPG -->\n\nWorld");
      expect(html).toContain("data-page-anchor");
      expect(html).toContain('data-page="DSC0001.JPG"');
    });
  });

  describe("htmlToMarkdown", () => {
    it("converts headings back", () => {
      expect(htmlToMarkdown("<h1>Title</h1>")).toContain("# Title");
      expect(htmlToMarkdown("<h2>Sub</h2>")).toContain("## Sub");
    });

    it("converts page anchor div back to comment", () => {
      // Tiptap renderHTML 会给 div 加内容（icon + label），让 turndown 不
      // 把它当 blank 丢掉。markdownToHtml 也产出非空 div，与此一致。
      const html
        = '<div data-page-anchor data-page="DSC0001.JPG">'
        + '<span>📄</span><span>DSC0001.JPG</span></div>';
      const md = htmlToMarkdown(html);
      expect(md).toContain("<!-- page: DSC0001.JPG -->");
    });

    it("converts GFM table back", () => {
      const html
        = "<table><thead><tr><th>a</th><th>b</th></tr></thead>"
        + "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>";
      const md = htmlToMarkdown(html);
      expect(md).toContain("| a |");
      expect(md).toContain("| b |");
    });
  });

  describe("round-trip", () => {
    it("preserves headings + page anchor through round-trip", () => {
      const md0 = "# 文档标题\n\n<!-- page: DSC0001.JPG -->\n\n第一段。\n";
      const html1 = markdownToHtml(md0);
      const md1 = htmlToMarkdown(html1);
      expect(md1).toContain("# 文档标题");
      expect(md1).toContain("<!-- page: DSC0001.JPG -->");
      expect(md1).toContain("第一段");
    });

    it("preserves GFM table through round-trip", () => {
      const md0 = "| 名称 | 数值 |\n|---|---|\n| A | 1 |\n| B | 2 |\n";
      const md1 = htmlToMarkdown(markdownToHtml(md0));
      // turndown 不保证 cell 间距 byte-equal，断言关键内容
      expect(md1).toMatch(/\|\s*名称\s*\|\s*数值\s*\|/);
      expect(md1).toMatch(/\|\s*A\s*\|\s*1\s*\|/);
      expect(md1).toMatch(/\|\s*B\s*\|\s*2\s*\|/);
    });

    it("preserves bold/italic/strike", () => {
      const md0 = "**bold** *italic* ~~strike~~\n";
      const md1 = htmlToMarkdown(markdownToHtml(md0));
      expect(md1).toMatch(/\*\*bold\*\*/);
      expect(md1).toMatch(/\*italic\*|_italic_/);
      // turndown-plugin-gfm 用单 ~（GFM 也接受单 ~）；marked 输入双 ~ 也
      // 解析成 <del>，所以语义保持，只是序列化形态不同。
      expect(md1).toMatch(/~+strike~+/);
    });

    it("preserves bullet list", () => {
      const md0 = "- one\n- two\n- three\n";
      const md1 = htmlToMarkdown(markdownToHtml(md0));
      expect(md1).toContain("one");
      expect(md1).toContain("two");
      expect(md1).toContain("three");
      // turndown 默认用 - / 1. 作为列表标记
      expect(md1).toMatch(/^-\s/m);
    });
  });
});
