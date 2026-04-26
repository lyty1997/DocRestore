/**
 * markdown 图片 URL 重写单元测试
 */

import { describe, expect, it } from "vitest";

import {
  escapeNonHtmlTags,
  preprocessMarkdown,
  rewriteImageUrls,
} from "../../../src/features/task/markdown";

describe("rewriteImageUrls", () => {
  const taskId = "abc-123";

  it("重写 images/ 开头的图片引用", () => {
    const input = "![alt](images/foo_1.jpg)";
    const result = rewriteImageUrls(input, taskId);
    expect(result).toBe(
      "![alt](/api/v1/tasks/abc-123/assets/images/foo_1.jpg)",
    );
  });

  it("重写多个图片引用", () => {
    const input = [
      "# 标题",
      "![](images/a_1.jpg)",
      "正文内容",
      "![图2](images/b_2.png)",
    ].join("\n");
    const result = rewriteImageUrls(input, taskId);
    expect(result).toContain("/api/v1/tasks/abc-123/assets/images/a_1.jpg");
    expect(result).toContain("/api/v1/tasks/abc-123/assets/images/b_2.png");
  });

  it("不修改已经是绝对路径的引用", () => {
    const input = "![alt](https://example.com/img.jpg)";
    const result = rewriteImageUrls(input, taskId);
    expect(result).toBe(input);
  });

  it("不修改非 images/ 开头的相对路径", () => {
    const input = "![alt](other/path.jpg)";
    const result = rewriteImageUrls(input, taskId);
    expect(result).toBe(input);
  });

  it("无图片引用时原样返回", () => {
    const input = "# 纯文本\n没有图片";
    const result = rewriteImageUrls(input, taskId);
    expect(result).toBe(input);
  });

  it("移除 OCR 中间产物的 markdown 引用（XXX_OCR/images/...）", () => {
    const input = "前\n![](task1_OCR/images/x.jpg)\n后";
    const result = rewriteImageUrls(input, taskId);
    expect(result).not.toContain("_OCR/images/");
    expect(result).toContain("前");
    expect(result).toContain("后");
  });

  it("移除 OCR 中间产物的 HTML <img> 标签", () => {
    const input = '<img src="abc_OCR/images/y.png" alt="x" />';
    const result = rewriteImageUrls(input, taskId);
    expect(result).not.toContain("_OCR/images/");
  });

  it("docDir 提供时给 images 路径加前缀", () => {
    const input = "![alt](images/foo.jpg)";
    const result = rewriteImageUrls(input, taskId, "doc1");
    expect(result).toContain("/api/v1/tasks/abc-123/assets/doc1/images/foo.jpg");
  });

  it("HTML <img src=\"images/...\"> 也被重写", () => {
    const input = '<img class="x" src="images/foo.jpg" />';
    const result = rewriteImageUrls(input, taskId);
    expect(result).toContain("/api/v1/tasks/abc-123/assets/images/foo.jpg");
    expect(result).toContain('<img class="x"');
  });
});

describe("escapeNonHtmlTags", () => {
  it("保留白名单标签（如 img/table/td）", () => {
    const input = '<img src="x"><table><tr><td>1</td></tr></table>';
    expect(escapeNonHtmlTags(input)).toBe(input);
  });

  it("转义非白名单标签（如 <Select>）", () => {
    expect(escapeNonHtmlTags("点击 <Select> 按钮")).toBe(
      "点击 &lt;Select&gt; 按钮",
    );
  });

  it("转义自定义/未知大小写标签的闭合", () => {
    expect(escapeNonHtmlTags("<Foo>x</Foo>")).toBe("&lt;Foo&gt;x&lt;/Foo&gt;");
  });

  it("白名单标签的关闭标签也保留", () => {
    expect(escapeNonHtmlTags("<div>hi</div>")).toBe("<div>hi</div>");
  });
});

describe("preprocessMarkdown", () => {
  it("先重写图片再转义非白名单标签", () => {
    const input = "![](images/a.jpg)\n点击 <Exit>";
    const result = preprocessMarkdown(input, "abc-123");
    expect(result).toContain("/api/v1/tasks/abc-123/assets/images/a.jpg");
    expect(result).toContain("&lt;Exit&gt;");
  });

  it("docDir 透传到底层重写函数", () => {
    const result = preprocessMarkdown("![](images/a.jpg)", "abc-123", "doc1");
    expect(result).toContain(
      "/api/v1/tasks/abc-123/assets/doc1/images/a.jpg",
    );
  });
});
