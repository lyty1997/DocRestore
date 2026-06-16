/**
 * 预览 HTML sanitize 白名单测试（#46）：
 * - 剥离不可信 HTML 的 XSS 载荷（事件处理器 / javascript: 协议）
 * - 保留页锚点 span 的 data-page（滚动同步依赖 [data-page]）与正常表格
 */

import { cleanup, render } from "@testing-library/react";
import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { afterEach, describe, expect, it } from "vitest";

import { PREVIEW_SANITIZE_SCHEMA } from "../../../src/features/task/markdownSanitize";

afterEach(cleanup);

function renderMarkdown(md: string): HTMLElement {
  const { container } = render(
    <Markdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, PREVIEW_SANITIZE_SCHEMA]]}
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
