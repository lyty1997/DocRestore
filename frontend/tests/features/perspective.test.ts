import { describe, expect, it } from "vitest";

import {
  matrix3dFromQuad,
  projectPoint,
  quadToRectProjection,
} from "../../src/features/task/perspective";

function expectClose(
  got: { x: number; y: number }, ex: number, ey: number,
): void {
  expect(got.x).toBeCloseTo(ex, 4);
  expect(got.y).toBeCloseTo(ey, 4);
}

describe("perspective", () => {
  it("quadToRectProjection 把四边形 4 角精确映射到正矩形 4 角", () => {
    const tl = { x: 10, y: 20 };
    const tr = { x: 110, y: 30 };
    const br = { x: 120, y: 140 };
    const bl = { x: 5, y: 130 };
    const w = 200;
    const h = 100;
    const m = quadToRectProjection(tl, tr, br, bl, w, h);

    expectClose(projectPoint(m, tl), 0, 0);
    expectClose(projectPoint(m, tr), w, 0);
    expectClose(projectPoint(m, br), w, h);
    expectClose(projectPoint(m, bl), 0, h);
  });

  it("轴对齐矩形（无透视）：中点线性映射到矩形中心", () => {
    // 源为轴对齐矩形 → 投影退化为仿射，中点应映射到目标中心
    const m = quadToRectProjection(
      { x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 50 }, { x: 0, y: 50 },
      200, 100,
    );
    const mid = projectPoint(m, { x: 50, y: 25 });
    expect(mid.x).toBeCloseTo(100, 4);
    expect(mid.y).toBeCloseTo(50, 4);
  });

  it("matrix3dFromQuad 返回含 16 个数的 matrix3d 字符串", () => {
    const css = matrix3dFromQuad(
      { x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 50 }, { x: 0, y: 50 },
      100, 50,
    );
    expect(css.startsWith("matrix3d(")).toBe(true);
    const inner = css.slice("matrix3d(".length, -1);
    expect(inner.split(",")).toHaveLength(16);
    // 每一项都是有限数（无 NaN / Infinity）
    for (const part of inner.split(",")) {
      expect(Number.isFinite(Number(part))).toBe(true);
    }
  });
});
