import { describe, expect, it } from "vitest";

import { computeLineWindow } from "../../../src/features/task/lineWindow";

describe("computeLineWindow", () => {
  const ROW = 20;
  const VIEWPORT = 200; // 10 行可视
  const OVERSCAN = 8;

  it("滚到顶部时从 0 开始", () => {
    const { start, end } = computeLineWindow(0, VIEWPORT, ROW, 1000, OVERSCAN);
    expect(start).toBe(0);
    // ceil(200/20)=10 + overscan 8 = 18
    expect(end).toBe(18);
  });

  it("滚动到中部时窗口随之平移并带上下 overscan", () => {
    // scrollTop=2000 → floor(2000/20)=100；start=100-8=92
    // end=ceil((2000+200)/20)+8=110+8=118
    const { start, end } = computeLineWindow(2000, VIEWPORT, ROW, 1000, OVERSCAN);
    expect(start).toBe(92);
    expect(end).toBe(118);
  });

  it("窗口在文件末尾被钳制到 totalLines", () => {
    const { start, end } = computeLineWindow(19_600, VIEWPORT, ROW, 1000, OVERSCAN);
    // floor(19600/20)=980；start=972；end=min(1000, ceil(19800/20)+8)=min(1000,998)=998
    expect(start).toBe(972);
    expect(end).toBe(998);
    expect(end).toBeLessThanOrEqual(1000);
  });

  it("渲染行数远小于总行数（虚拟化生效）", () => {
    const { start, end } = computeLineWindow(10_000, VIEWPORT, ROW, 5000, OVERSCAN);
    expect(end - start).toBeLessThan(40);
  });

  it("行高非法（<=0）时退化为整文件渲染", () => {
    expect(computeLineWindow(100, VIEWPORT, 0, 500, OVERSCAN)).toEqual({
      start: 0,
      end: 500,
    });
  });

  it("空文件返回空区间", () => {
    expect(computeLineWindow(0, VIEWPORT, ROW, 0, OVERSCAN)).toEqual({
      start: 0,
      end: 0,
    });
  });

  it("视口高度为 0 时 end 不小于 start", () => {
    const { start, end } = computeLineWindow(2000, 0, ROW, 1000, OVERSCAN);
    expect(end).toBeGreaterThanOrEqual(start);
  });

  it("负的 scrollTop 被当作 0 处理", () => {
    const { start } = computeLineWindow(-50, VIEWPORT, ROW, 1000, OVERSCAN);
    expect(start).toBe(0);
  });
});
