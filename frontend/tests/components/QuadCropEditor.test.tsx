import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QuadCropEditor } from "../../src/components/QuadCropEditor";

afterEach(cleanup);

describe("QuadCropEditor", () => {
  it("4 个角点按原图坐标换算成百分比定位", () => {
    const { container } = render(
      <QuadCropEditor
        imageUrl="x.jpg"
        naturalWidth={400}
        naturalHeight={400}
        quad={{
          tl: { x: 100, y: 50 },
          tr: { x: 300, y: 40 },
          br: { x: 320, y: 250 },
          bl: { x: 80, y: 240 },
        }}
        onChange={vi.fn()}
      />,
    );
    const handles = container.querySelectorAll<HTMLElement>(
      ".quad-editor-handle",
    );
    expect(handles).toHaveLength(4);
    // DOM 顺序固定 tl, tr, br, bl
    expect(handles[0]?.style.left).toBe("25%"); // tl 100/400
    expect(handles[0]?.style.top).toBe("12.5%"); // tl 50/400
    expect(handles[2]?.style.left).toBe("80%"); // br 320/400
    expect(handles[2]?.style.top).toBe("62.5%"); // br 250/400
  });

  it("画出四边形 polygon（点序 tl tr br bl）", () => {
    const { container } = render(
      <QuadCropEditor
        imageUrl="x.jpg"
        naturalWidth={200}
        naturalHeight={200}
        quad={{
          tl: { x: 10, y: 20 },
          tr: { x: 180, y: 15 },
          br: { x: 190, y: 170 },
          bl: { x: 5, y: 160 },
        }}
        onChange={vi.fn()}
      />,
    );
    const poly = container.querySelector(".quad-editor-poly");
    expect(poly).not.toBeNull();
    expect(poly?.getAttribute("points")).toBe(
      "10,20 180,15 190,170 5,160",
    );
  });
});
