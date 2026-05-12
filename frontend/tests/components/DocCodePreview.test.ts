import { describe, expect, it } from "vitest";

import {
  extractPageNamesFromMarkdown,
  filterImagesForDoc,
} from "../../src/features/task/sourceImages";

describe("DocCodePreview source image filtering", () => {
  it("按 markdown page marker 过滤边界拆分出的扁平多文档源图", () => {
    const images = ["p1.jpg", "p2.jpg", "p3.jpg"];
    const markdown = [
      "<!-- page: p2.jpg -->",
      "第二篇正文",
      "<!-- page: p3.jpg -->",
    ].join("\n");

    expect(filterImagesForDoc(images, "报告B", markdown)).toEqual([
      "p2.jpg",
      "p3.jpg",
    ]);
  });

  it("子目录加边界拆分时剥掉输出标题目录后匹配源图前缀", () => {
    const images = [
      "section/p1.jpg",
      "section/p2.jpg",
      "other/p2.jpg",
      "other/p3.jpg",
    ];
    const markdown = [
      "<!-- page: p2.jpg -->",
      "section 下第二篇正文",
    ].join("\n");

    expect(filterImagesForDoc(images, "section/报告B", markdown)).toEqual([
      "section/p2.jpg",
    ]);
  });

  it("没有 page marker 时保留旧的 doc_dir 前缀过滤", () => {
    const images = ["a/p1.jpg", "a/p2.jpg", "b/p3.jpg"];

    expect(filterImagesForDoc(images, "a", "# 无页标记")).toEqual([
      "a/p1.jpg",
      "a/p2.jpg",
    ]);
  });

  it("提取 page marker 时保留顺序并去重", () => {
    const markdown = [
      "<!-- page: p1.jpg -->",
      "正文",
      "<!-- page: p2.jpg -->",
      "<!-- page: p1.jpg -->",
    ].join("\n");

    expect(extractPageNamesFromMarkdown(markdown)).toEqual([
      "p1.jpg",
      "p2.jpg",
    ]);
  });
});
