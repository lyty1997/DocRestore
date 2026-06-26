/**
 * 代码模式悬停行放大纯函数 ``codeLineMagnifier``（#93 · F2）测试。
 *
 * 覆盖 ``buildLineIndex`` 建索引 + 同 line_no 去重；``computeMagnifierRegion``
 * 命中并集 / 首尾边界 / 跨页只并同页 / 当前行无 bbox 就近回退 / 就近窗口外无 →
 * undefined / 空索引与 undefined 退化。断言从构造输入派生。
 */

import { describe, expect, it } from "vitest";

import type { CodeLayoutPayload } from "../../src/api/schemas";
import {
  buildLineIndex,
  computeMagnifierRegion,
  type FileLineIndex,
  type LineBox,
  type LineBoxTuple,
  lineIndexAtOffset,
} from "../../src/features/task/codeLineMagnifier";

function box(page: string, bbox: LineBoxTuple): LineBox {
  return { page, bbox };
}

function fidx(entries: readonly (readonly [number, LineBox])[]): FileLineIndex {
  return new Map(entries);
}

describe("buildLineIndex", () => {
  it("建 path→line_no 索引；同 line_no 保留首个（去重防御）", () => {
    const payload: CodeLayoutPayload = {
      files: [{
        path: "app/foo.py",
        lines: [
          { line_no: 1, page: "p.col0", bbox: [0, 0, 10, 10] },
          { line_no: 1, page: "q.col0", bbox: [9, 9, 9, 9] },
          { line_no: 2, page: "p.col0", bbox: [0, 10, 10, 20] },
        ],
      }],
    };
    const index = buildLineIndex(payload);
    const file = index.get("app/foo.py");
    expect(file?.size).toBe(2);
    expect(file?.get(1)?.page).toBe("p.col0"); // 首个保留
    expect(file?.get(2)?.bbox).toEqual([0, 10, 10, 20]);
  });
});

describe("computeMagnifierRegion", () => {
  it("命中：当前行 ±1 同页 bbox 并集 + 当前行 focus", () => {
    const fi = fidx([
      [1, box("pA", [0, 0, 10, 20])],
      [2, box("pA", [0, 20, 10, 40])],
      [3, box("pA", [0, 40, 10, 60])],
    ]);
    expect(computeMagnifierRegion(fi, 2)).toEqual({
      page: "pA",
      region: { x0: 0, y0: 0, x1: 10, y1: 60 },
      focus: [0, 20, 10, 40], // 整行带：x 取全页行宽 [0..10]，y 取当前行 [20..40]
    });
  });

  it("同页不同行宽：横向恒取当前页行宽并集，短行不铺开", () => {
    const fi = fidx([
      [1, box("pA", [10, 0, 200, 20])], // 长行（x1=200）
      [2, box("pA", [10, 20, 60, 40])], // 短行（x1=60）
      [3, box("pA", [10, 40, 120, 60])],
    ]);
    // 悬停短行 line2：region.x 仍取全页 [10..200]（非短行的 60），缩放不随行长变
    const target = computeMagnifierRegion(fi, 2);
    expect(target?.region).toEqual({ x0: 10, y0: 0, x1: 200, y1: 60 });
    // focus 横向铺满全页行宽 [10..200]（整行背景带，不取短行真实宽 60），纵向用短行真实 y
    expect(target?.focus).toEqual([10, 20, 200, 40]);
  });

  it("首行边界：只并 line 与 line+1（无 line-1）", () => {
    const fi = fidx([
      [1, box("pA", [0, 0, 10, 20])],
      [2, box("pA", [0, 20, 10, 40])],
    ]);
    expect(computeMagnifierRegion(fi, 1)).toEqual({
      page: "pA",
      region: { x0: 0, y0: 0, x1: 10, y1: 40 },
      focus: [0, 0, 10, 20],
    });
  });

  it("跨页边界：相邻行在别页 → 只并同页那侧", () => {
    const fi = fidx([
      [1, box("pA", [0, 0, 10, 20])],
      [2, box("pA", [0, 20, 10, 40])],
      [3, box("pB", [0, 0, 10, 20])], // 翻页
    ]);
    // 悬停 line2（pA）→ 并 line1+line2，排除 line3（pB）
    expect(computeMagnifierRegion(fi, 2)).toEqual({
      page: "pA",
      region: { x0: 0, y0: 0, x1: 10, y1: 40 },
      focus: [0, 20, 10, 40],
    });
    // 悬停 line3（pB）→ 仅 line3（line2 在 pA 排除，line4 无）
    expect(computeMagnifierRegion(fi, 3)).toEqual({
      page: "pB",
      region: { x0: 0, y0: 0, x1: 10, y1: 20 },
      focus: [0, 0, 10, 20],
    });
  });

  it("当前行无 bbox（gap）→ 就近相邻行回退，并入同页相邻", () => {
    const fi = fidx([
      [1, box("pA", [0, 0, 10, 20])],
      // line 2 缺失（gap 占位 / 推断行）
      [3, box("pA", [0, 40, 10, 60])],
    ]);
    // anchor 就近取 line1（pA），±1 命中 line1+line3；focus 用就近行 line1
    expect(computeMagnifierRegion(fi, 2)).toEqual({
      page: "pA",
      region: { x0: 0, y0: 0, x1: 10, y1: 60 },
      focus: [0, 0, 10, 20],
    });
  });

  it("当前行无 bbox 且相邻也无 → 用就近行单框兜底", () => {
    const fi = fidx([[7, box("pB", [1, 2, 3, 4])]]);
    // line4 周围 ±1 都无 → findNearest 扫到 line7 → 单框，focus 同为就近行
    expect(computeMagnifierRegion(fi, 4)).toEqual({
      page: "pB",
      region: { x0: 1, y0: 2, x1: 3, y1: 4 },
      focus: [1, 2, 3, 4],
    });
  });

  it("就近窗口外无任何 bbox → undefined（不放大优于错放）", () => {
    const fi = fidx([[1, box("pA", [0, 0, 10, 20])]]);
    expect(computeMagnifierRegion(fi, 100)).toBeUndefined();
  });

  it("空索引 / undefined → undefined", () => {
    expect(computeMagnifierRegion(new Map(), 1)).toBeUndefined();
    expect(computeMagnifierRegion(undefined, 1)).toBeUndefined();
  });
});

describe("lineIndexAtOffset", () => {
  const text = "aa\nbb\ncc"; // 行0: aa(0..2) \n(2) 行1: bb(3..5) \n(5) 行2: cc(6..8)

  it("偏移 0 / 空文本 → 第 0 行", () => {
    expect(lineIndexAtOffset(text, 0)).toBe(0);
    expect(lineIndexAtOffset("", 0)).toBe(0);
  });

  it("首行内偏移 → 第 0 行（换行字符本身仍属上一行末尾）", () => {
    expect(lineIndexAtOffset(text, 1)).toBe(0); // 'a' 之后
    expect(lineIndexAtOffset(text, 2)).toBe(0); // 第一个 \n 之前
  });

  it("跨过换行后偏移 → 下一行", () => {
    expect(lineIndexAtOffset(text, 3)).toBe(1); // 第一个 \n 之后，'bb' 行首
    expect(lineIndexAtOffset(text, 4)).toBe(1);
    expect(lineIndexAtOffset(text, 6)).toBe(2); // 第二个 \n 之后，'cc' 行首
  });

  it("末尾偏移 → 末行", () => {
    expect(lineIndexAtOffset(text, text.length)).toBe(2);
  });

  it("越界偏移按 [0, len] 钳制", () => {
    expect(lineIndexAtOffset(text, -5)).toBe(0);
    expect(lineIndexAtOffset(text, 999)).toBe(2);
  });
});
