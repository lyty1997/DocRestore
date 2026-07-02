/**
 * 版面块分类 → 颜色 ``categoryColor``（layoutCategory.ts）测试（E8/#92）。
 */

import { describe, expect, it } from "vitest";

import { categoryColor } from "../../src/features/task/layoutCategory";

describe("categoryColor", () => {
  it("四大语义组各着不同色（标题 / 正文 / 表格 / 图）", () => {
    const borders = ["paragraph_title", "text", "table", "image"].map(
      (l) => categoryColor(l).border,
    );
    expect(new Set(borders).size).toBe(4);
  });

  it("同语义组共用一色（title 与 paragraph_title、chart 与 image）", () => {
    expect(categoryColor("title").border).toBe(
      categoryColor("paragraph_title").border,
    );
    expect(categoryColor("chart").border).toBe(categoryColor("image").border);
  });

  it("border 实色 rgb / fill 同色 rgba 带 0.15 alpha", () => {
    const c = categoryColor("text");
    expect(c.border).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
    expect(c.fill).toMatch(/^rgba\(\d+, \d+, \d+, 0\.15\)$/);
    // fill 与 border 同一 rgb 三元（仅多 alpha）
    expect(c.fill).toContain(c.border.slice(4, -1));
  });

  it("未知 / 空 label → fallback 灰（稳定一致）", () => {
    const unknown = categoryColor("no_such_label_xyz");
    expect(unknown.border).toBe(categoryColor("").border);
    expect(unknown.border).toBe("rgb(120, 120, 120)");
  });
});
