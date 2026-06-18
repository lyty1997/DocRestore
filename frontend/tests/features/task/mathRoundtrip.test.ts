/**
 * 编辑器数学公式 round-trip 保真测试（阶段 1）。
 *
 * 核心断言：md → html →（Tiptap）→ html → md 全链路，公式 LaTeX 逐字符不变，
 * marked / turndown 都不碰 `_` `\` `\\` 等（latex 只在 data-latex 属性里）。
 */

import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { describe, expect, it } from "vitest";

import {
  htmlToMarkdown,
  markdownToHtml,
} from "../../../src/features/task/markdownRoundtrip";
import { MathBlock, MathInline } from "../../../src/features/task/mathNodes";

/** 串接两侧（不经 Tiptap）：验证 marked/turndown 两端口径一致。 */
function stringRoundtrip(md: string): string {
  return htmlToMarkdown(markdownToHtml(md));
}

/** 全链路（经 Tiptap parse → render）：验证节点保住 data-latex。 */
function editorRoundtrip(md: string): string {
  const editor = new Editor({
    extensions: [StarterKit, MathInline, MathBlock],
    content: markdownToHtml(md),
  });
  try {
    return htmlToMarkdown(editor.getHTML());
  } finally {
    editor.destroy();
  }
}

describe("markdownToHtml 公式抽取", () => {
  it("行内 $...$ → data-math-inline + data-latex（下标不被当强调破坏）", () => {
    const html = markdownToHtml("前 $a_b$ 后");
    expect(html).toContain("data-math-inline");
    expect(html).toContain('data-latex="a_b"');
  });

  it("块级 $$...$$ → data-math-display；不被行内规则拆成两个 $", () => {
    const html = markdownToHtml("$$x^2$$");
    expect(html).toContain("data-math-display");
    expect(html).not.toContain("data-math-inline");
  });

  it("反斜杠命令进 data-latex 不被 marked 转义吞掉", () => {
    const html = markdownToHtml(String.raw`公式 $\alpha$ 收尾`);
    expect(html).toContain(String.raw`data-latex="\alpha"`);
  });
});

describe("htmlToMarkdown 公式还原", () => {
  it("data-math-inline → $...$（只读 data-latex）", () => {
    const md = htmlToMarkdown(
      '<p><span data-math-inline data-latex="a_b">x</span></p>',
    );
    expect(md).toContain("$a_b$");
  });

  it("data-math-display → $$...$$", () => {
    const md = htmlToMarkdown(
      '<div data-math-display data-latex="E=mc^2">x</div>',
    );
    expect(md).toContain("$$");
    expect(md).toContain("E=mc^2");
  });
});

describe("round-trip 保真（string 两端）", () => {
  it("行内含下标 + 反斜杠命令逐字保留", () => {
    const md = String.raw`行内 $\alpha_i$ 收尾`;
    const out = stringRoundtrip(md);
    expect(out).toContain(String.raw`$\alpha_i$`);
    // 二次 round-trip 稳定（幂等）
    expect(stringRoundtrip(out).trim()).toBe(out.trim());
  });

  it("块级矩阵换行符逐字保留", () => {
    const md = "$$\n" + String.raw`\begin{matrix}a&b\\c&d\end{matrix}` + "\n$$";
    const out = stringRoundtrip(md);
    expect(out).toContain(String.raw`\begin{matrix}a&b\\c&d\end{matrix}`);
  });
});

describe("round-trip 保真（经 Tiptap 全链路）", () => {
  it("行内公式过编辑器后 LaTeX 不变", () => {
    const md = String.raw`设 $f(x)=x^2_0$ 为函数`;
    const out = editorRoundtrip(md);
    expect(out).toContain(String.raw`$f(x)=x^2_0$`);
  });

  it("块级公式过编辑器后 LaTeX 不变（含换行与下标）", () => {
    const md = "$$\n" + String.raw`M=\mathbf{1}_{m\times m}\\N=0` + "\n$$";
    const out = editorRoundtrip(md);
    expect(out).toContain(String.raw`\mathbf{1}_{m\times m}\\N=0`);
  });

  it("行内公式、块级公式与正文共存，互不干扰", () => {
    const md = "正文 $x_1$ 文字\n\n$$y=k$$";
    const out = editorRoundtrip(md);
    expect(out).toContain("$x_1$"); // 行内保留
    expect(out).toContain("正文"); // 正文保留
    expect(out).toContain("y=k"); // 块级保留
    expect(out).toContain("$$"); // 块级仍是 $$ 形态
  });
});
