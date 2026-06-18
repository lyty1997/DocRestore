/**
 * 预览渲染管线测试（#46 sanitize + 数学公式渲染）：
 * - 剥离不可信 HTML 的 XSS 载荷（事件处理器 / javascript: 协议）
 * - 保留页锚点 span 的 data-page（滚动同步依赖 [data-page]）与正常表格
 * - `$...$` / `$$...$$` 经 remark-math + rehype-katex 渲染为 KaTeX
 *
 * 直接复用生产的 PREVIEW_REMARK_PLUGINS / PREVIEW_REHYPE_PLUGINS，
 * 保证测试与 DocCodePreview 用的是同一条插件链（含顺序）。
 */

import { cleanup, render } from "@testing-library/react";
import Markdown from "react-markdown";
import { afterEach, describe, expect, it } from "vitest";

import { preprocessMarkdown } from "../../../src/features/task/markdown";
import {
  PREVIEW_REHYPE_PLUGINS,
  PREVIEW_REMARK_PLUGINS,
} from "../../../src/features/task/markdownSanitize";

afterEach(cleanup);

function renderMarkdown(md: string): HTMLElement {
  const { container } = render(
    <Markdown
      remarkPlugins={PREVIEW_REMARK_PLUGINS}
      rehypePlugins={PREVIEW_REHYPE_PLUGINS}
    >
      {md}
    </Markdown>,
  );
  return container;
}

describe("PREVIEW_SANITIZE_SCHEMA", () => {
  it("保留页锚点 span 的 data-page（滚动同步依赖）", () => {
    const c = renderMarkdown(
      '<span class="page-anchor" data-page="a.jpg"></span>',
    );
    const anchor = c.querySelector<HTMLElement>("[data-page]");
    expect(anchor).not.toBeNull();
    expect(anchor?.dataset.page).toBe("a.jpg");
  });

  it("剥离 <img onerror> 事件处理器", () => {
    const c = renderMarkdown('<img src="x" onerror="alert(1)">');
    const img = c.querySelector("img");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("onerror")).toBeNull();
  });

  it("中和 javascript: 链接协议", () => {
    const c = renderMarkdown('<a href="javascript:alert(1)">x</a>');
    const href = c.querySelector("a")?.getAttribute("href") ?? "";
    expect(href).not.toContain("javascript:");
  });

  it("正常表格 HTML 不回归", () => {
    const c = renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |");
    expect(c.querySelector("table")).not.toBeNull();
    expect(c.querySelectorAll("td")).toHaveLength(2);
  });
});

describe("数学公式渲染（KaTeX）", () => {
  it("独占行 $$ 块级公式渲染为 KaTeX（含 display 容器与 TeX 注解）", () => {
    // 块级 display 需 $$ 独占成行
    const c = renderMarkdown("$$\nE = mc^2\n$$");
    expect(c.querySelector(".katex")).not.toBeNull();
    expect(c.querySelector(".katex-display")).not.toBeNull();
    // 不再以原始 $$ 文本残留
    expect(c.textContent).not.toContain("$$");
    // MathML 注解保留原始 TeX 源（断言从输入派生）
    const annotation = c.querySelector(
      'annotation[encoding="application/x-tex"]',
    );
    expect(annotation?.textContent).toContain("mc^2");
  });

  it("单行 $$...$$ 经 preprocessMarkdown 规范化后渲染为块级 display", () => {
    // OCR 常把块级公式压成一行；preprocessMarkdown 应拆成独占行后渲成 display
    const pre = preprocessMarkdown("$$ E = mc^2 $$", "task-1");
    const c = renderMarkdown(pre);
    expect(c.querySelector(".katex-display")).not.toBeNull();
  });

  it("$...$ 行内公式渲染为 KaTeX 且非 display", () => {
    const c = renderMarkdown("前 $a + b$ 后");
    expect(c.querySelector(".katex")).not.toBeNull();
    expect(c.querySelector(".katex-display")).toBeNull();
  });

  it("不规范 LaTeX 不抛错崩页（throwOnError=false 容错）", () => {
    // 模拟 OCR 产物：矩阵用 \ 当换行、缺下标——KaTeX 应容错而非抛异常
    const bad = String.raw`$$\left[\begin{matrix}{\mathbf{1}{m}}&{\mathbf{0}}\ \end{matrix}\right]$$`;
    expect(() => renderMarkdown(bad)).not.toThrow();
    const c = renderMarkdown(bad);
    // 容错后仍产出元素，且不残留原始 $$
    expect(c.textContent).not.toContain("$$");
  });

  it("KaTeX 渲染后 sanitize 仍生效（块级公式与脚本同存时剥离脚本）", () => {
    const c = renderMarkdown("$$x^2$$\n\n<script>alert(1)</script>");
    expect(c.querySelector(".katex")).not.toBeNull();
    expect(c.querySelector("script")).toBeNull();
  });
});
