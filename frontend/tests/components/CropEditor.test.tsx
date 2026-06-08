import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CropEditor } from "../../src/components/CropEditor";

afterEach(cleanup);

describe("CropEditor", () => {
  it("框按原图坐标换算成百分比定位 + 渲染 8 个手柄", () => {
    const { container } = render(
      <CropEditor
        imageUrl="x.jpg"
        naturalWidth={1000}
        naturalHeight={800}
        box={{ x0: 200, y0: 0, x1: 800, y1: 800 }}
        onChange={vi.fn()}
      />,
    );
    const box = container.querySelector<HTMLElement>(".crop-editor-box");
    expect(box).not.toBeNull();
    expect(box?.style.left).toBe("20%"); // 200 / 1000
    expect(box?.style.top).toBe("0%");
    expect(box?.style.width).toBe("60%"); // 600 / 1000
    expect(box?.style.height).toBe("100%"); // 800 / 800
    expect(container.querySelectorAll(".crop-editor-handle")).toHaveLength(8);
  });

  it("不同框尺寸换算正确（含小数）", () => {
    const { container } = render(
      <CropEditor
        imageUrl="x.jpg"
        naturalWidth={400}
        naturalHeight={400}
        box={{ x0: 100, y0: 50, x1: 300, y1: 350 }}
        onChange={vi.fn()}
      />,
    );
    const box = container.querySelector<HTMLElement>(".crop-editor-box");
    expect(box?.style.left).toBe("25%"); // 100 / 400
    expect(box?.style.top).toBe("12.5%"); // 50 / 400
    expect(box?.style.width).toBe("50%"); // 200 / 400
    expect(box?.style.height).toBe("75%"); // 300 / 400
  });
});
