/**
 * markdown 图片 URL 重写单元测试
 */

import { describe, expect, it } from "vitest";

import { rewriteImageUrls } from "../../../src/features/task/markdown";

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
