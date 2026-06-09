/**
 * markdown 图片 URL 重写单元测试
 */

import { describe, expect, it } from "vitest";

import {
  editorAssetUrlsToImages,
  editorImagesToAssetUrls,
  injectPageAnchors,
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
});

describe("injectPageAnchors", () => {
  it("把单个 page 注释转成隐藏锚点 span", () => {
    const input = "段前\n<!-- page: page04696.jpg -->\n段后";
    const out = injectPageAnchors(input);
    expect(out).toContain(
      '<span class="page-anchor" data-page="page04696.jpg"></span>',
    );
    expect(out).not.toContain("<!--");
  });

  it("多个 page 标记都被替换", () => {
    const input = [
      "<!-- page: a.jpg -->",
      "# H1",
      "<!-- page: b.png -->",
      "text",
    ].join("\n");
    const out = injectPageAnchors(input);
    expect(out).toContain('data-page="a.jpg"');
    expect(out).toContain('data-page="b.png"');
    expect(out).not.toContain("<!-- page:");
  });

  it("兼容文件名含单个连字符", () => {
    const input = "<!-- page: page04696-2.jpg -->";
    const out = injectPageAnchors(input);
    expect(out).toContain('data-page="page04696-2.jpg"');
  });

  it("兼容文件名前后含空白", () => {
    const input = "<!--   page:   foo.jpg   -->";
    const out = injectPageAnchors(input);
    expect(out).toContain('data-page="foo.jpg"');
  });

  it("page 标记带相对路径时锚点归一化为裸文件名", () => {
    const input = "<!-- page: section/foo.jpg -->";
    const out = injectPageAnchors(input);
    expect(out).toContain('data-page="foo.jpg"');
    expect(out).not.toContain('data-page="section/foo.jpg"');
  });

  it("无 page 标记时原样返回", () => {
    const input = "# 标题\n正文无标记";
    expect(injectPageAnchors(input)).toBe(input);
  });

  it("不动其他 HTML 注释", () => {
    const input = "<!-- TODO: refactor -->\n<!-- page: x.jpg -->";
    const out = injectPageAnchors(input);
    expect(out).toContain("<!-- TODO: refactor -->");
    expect(out).toContain('data-page="x.jpg"');
  });

  it("data-page 内引号被转义（防 attr 注入）", () => {
    const input = '<!-- page: evil".jpg -->';
    const out = injectPageAnchors(input);
    expect(out).toContain('data-page="evil&quot;.jpg"');
  });
});

describe("preprocessMarkdown (集成：锚点 + 图片重写 + 标签转义)", () => {
  const taskId = "abc-123";

  it("同时注入锚点并重写图片", () => {
    const input = [
      "<!-- page: p1.jpg -->",
      "![](images/foo.jpg)",
    ].join("\n");
    const out = preprocessMarkdown(input, taskId);
    expect(out).toContain('data-page="p1.jpg"');
    expect(out).toContain("/api/v1/tasks/abc-123/assets/images/foo.jpg");
  });

  it("注释先转锚点 → 不会被标签白名单转义破坏", () => {
    const input = "<!-- page: a.jpg -->\n<Unknown>tag</Unknown>";
    const out = preprocessMarkdown(input, taskId);
    // 锚点保留
    expect(out).toContain('<span class="page-anchor" data-page="a.jpg">');
    // 非白名单 <Unknown> 被转义
    expect(out).toContain("&lt;Unknown&gt;");
  });
});

describe("editor 图片 URL 正逆变换", () => {
  const taskId = "abc-123";

  it("forward：images/ → asset URL（markdown）", () => {
    const out = editorImagesToAssetUrls("![alt](images/foo_1.jpg)", taskId);
    expect(out).toContain("/api/v1/tasks/abc-123/assets/images/foo_1.jpg");
  });

  it("forward：多文档加 docDir 前缀", () => {
    const out = editorImagesToAssetUrls("![](images/x.jpg)", taskId, "doc-1");
    expect(out).toContain("/api/v1/tasks/abc-123/assets/doc-1/images/x.jpg");
  });

  it("forward 不剥离 _OCR/images 中间产物引用（与 rewriteImageUrls 不同）", () => {
    const input = "![](sub_OCR/images/3.jpg)";
    // 只匹配以 images/ 开头的引用，_OCR/images 原样保留
    expect(editorImagesToAssetUrls(input, taskId)).toBe(input);
  });

  it("round-trip：forward 后 reverse 还原回相对 images/（无 docDir）", () => {
    const input = "![alt](images/foo_1.jpg)";
    const fwd = editorImagesToAssetUrls(input, taskId);
    expect(editorAssetUrlsToImages(fwd, taskId)).toBe(input);
  });

  it("reverse 丢掉 docDir 前缀，回到稳定的相对 images/", () => {
    const input = "![](images/x.jpg)";
    const fwd = editorImagesToAssetUrls(input, taskId, "doc-1");
    // 逆变换不带 docDir：还原成相对 images/（下次加载再补前缀）
    expect(editorAssetUrlsToImages(fwd, taskId)).toBe(input);
  });

  it("reverse 不动指向其他任务 / 外链的 URL", () => {
    const other = "![](/api/v1/tasks/zzz/assets/images/y.jpg)";
    const ext = "![](https://example.com/a.png)";
    expect(editorAssetUrlsToImages(other, taskId)).toBe(other);
    expect(editorAssetUrlsToImages(ext, taskId)).toBe(ext);
  });

  it("HTML <img> round-trip 同样还原", () => {
    const input = '<img src="images/manual_2.jpg" alt="">';
    const fwd = editorImagesToAssetUrls(input, taskId);
    expect(fwd).toContain("/api/v1/tasks/abc-123/assets/images/manual_2.jpg");
    expect(editorAssetUrlsToImages(fwd, taskId)).toBe(input);
  });

  it("taskId 缺失场景由调用方跳过；此处仅验证相对引用不被误伤", () => {
    // 已是相对路径、无 asset 前缀 → reverse 不应改动
    const input = "![](images/foo.jpg)";
    expect(editorAssetUrlsToImages(input, taskId)).toBe(input);
  });
});
