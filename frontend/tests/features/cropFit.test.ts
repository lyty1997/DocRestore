import { describe, expect, it } from "vitest";

import {
  fitRegion,
  quadBBox,
  type ViewTransform,
} from "../../src/features/task/cropFit";

/** 收窄 fitRegion 返回值（无 jest-dom，用抛错断言非 undefined）。 */
function mustFit(v: ViewTransform | undefined): ViewTransform {
  if (v === undefined) throw new Error("fitRegion 意外返回 undefined");
  return v;
}

describe("quadBBox", () => {
  it("取四个角点的外接矩形", () => {
    expect(
      quadBBox({
        tl: { x: 10, y: 40 },
        tr: { x: 90, y: 20 },
        br: { x: 100, y: 80 },
        bl: { x: 5, y: 70 },
      }),
    ).toEqual({ x0: 5, y0: 20, x1: 100, y1: 80 });
  });
});

describe("fitRegion", () => {
  // 视口 800×600，原图 1600×1200 → 基准缩放 s0=0.5、基准尺寸 800×600
  const vw = 800;
  const vh = 600;
  const iw = 1600;
  const ih = 1200;
  const whole = { x0: 0, y0: 0, x1: iw, y1: ih };

  it("视口或原图尺寸非正返回 undefined", () => {
    expect(fitRegion(0, vh, iw, ih, whole)).toBeUndefined();
    expect(fitRegion(vw, 0, iw, ih, whole)).toBeUndefined();
    expect(fitRegion(vw, vh, 0, ih, whole)).toBeUndefined();
  });

  it("区域=整图时 zoom=1 且内容与视口对齐", () => {
    const v = mustFit(fitRegion(vw, vh, iw, ih, whole));
    expect(v.baseWidth).toBe(800);
    expect(v.baseHeight).toBe(600);
    expect(v.zoom).toBe(1);
    expect(v.tx).toBe(0);
    expect(v.ty).toBe(0);
  });

  it("居中小框放大后框中心对准视口中心", () => {
    // 400×400 源图像素的框，中心 (800, 600)
    const v = mustFit(
      fitRegion(vw, vh, iw, ih, { x0: 600, y0: 400, x1: 1000, y1: 800 }),
    );
    // zFit = 0.78 * min(800/200, 600/200) = 2.34，未触封顶（2/s0=4）
    expect(v.zoom).toBeCloseTo(2.34, 5);
    // 框中心映射回视口中心：tx + cx*s0*zoom = vw/2
    expect(v.tx + 800 * 0.5 * v.zoom).toBeCloseTo(vw / 2, 5);
    expect(v.ty + 600 * 0.5 * v.zoom).toBeCloseTo(vh / 2, 5);
  });

  it("极小框放大封顶到 MAX_PIXEL_SCALE / s0", () => {
    const v = mustFit(
      fitRegion(vw, vh, iw, ih, { x0: 795, y0: 595, x1: 805, y1: 605 }),
    );
    expect(v.zoom).toBe(4); // 2 / 0.5
  });

  it("框贴边时平移夹取，不在图边内侧露空", () => {
    const v = mustFit(
      fitRegion(vw, vh, iw, ih, { x0: 0, y0: 0, x1: 400, y1: 400 }),
    );
    // 理想居中要求 tx>0（图左边离开视口左缘），夹取回 0
    expect(v.tx).toBe(0);
    expect(v.ty).toBe(0);
    // 内容右/下边不缩进视口内
    expect(v.tx + v.baseWidth * v.zoom).toBeGreaterThanOrEqual(vw);
    expect(v.ty + v.baseHeight * v.zoom).toBeGreaterThanOrEqual(vh);
  });

  it("width 模式：纵向整高框按宽度放大（both 模式下被钉在 1）", () => {
    // 纵向整高、宽 40% 的正文框：both 模式高度方向 0.78 < 1 → zoom=1
    const region = { x0: 480, y0: 0, x1: 1120, y1: ih };
    const both = mustFit(fitRegion(vw, vh, iw, ih, region));
    expect(both.zoom).toBe(1);
    // width 模式只看宽度：0.78 * 800 / (640*0.5) = 1.95
    const v = mustFit(fitRegion(vw, vh, iw, ih, region, "width"));
    expect(v.zoom).toBeCloseTo(1.95, 5);
    // 框中心水平对准视口中心；纵向溢出时中心对齐后夹取在合法范围
    expect(v.tx + 800 * 0.5 * v.zoom).toBeCloseTo(vw / 2, 5);
    expect(v.ty).toBeLessThanOrEqual(0);
    expect(v.ty + v.baseHeight * v.zoom).toBeGreaterThanOrEqual(vh);
  });

  it("zoom=1 下比视口短的维度居中", () => {
    // 宽图：1600×800 → s0=0.5、基准 800×400；整图区域不放大
    const v = mustFit(
      fitRegion(vw, vh, 1600, 800, { x0: 0, y0: 0, x1: 1600, y1: 800 }),
    );
    expect(v.zoom).toBe(1);
    expect(v.tx).toBe(0);
    expect(v.ty).toBe(100); // (600-400)/2
  });
});
