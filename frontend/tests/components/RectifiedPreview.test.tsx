import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RectifiedPreview } from "../../src/components/RectifiedPreview";

afterEach(cleanup);

describe("RectifiedPreview", () => {
  it("按四边形渲染 img 并套 matrix3d 变换，img 用源图自然尺寸", () => {
    const { container } = render(
      <RectifiedPreview
        imageUrl="x.jpg"
        naturalWidth={800}
        naturalHeight={600}
        quad={{
          tl: { x: 100, y: 100 },
          tr: { x: 500, y: 120 },
          br: { x: 480, y: 400 },
          bl: { x: 120, y: 380 },
        }}
      />,
    );
    const img = container.querySelector<HTMLImageElement>("img");
    expect(img).not.toBeNull();
    // 套了透视变换
    expect(img?.style.transform.startsWith("matrix3d(")).toBe(true);
    // img 以源图自然尺寸渲染（变换在其本地坐标系内进行）
    expect(img?.style.width).toBe("800px");
    expect(img?.style.height).toBe("600px");
    expect(img?.style.transformOrigin).toBe("0 0");
  });

  it("退化四边形（零面积）渲染空预览框，不报错", () => {
    const { container } = render(
      <RectifiedPreview
        imageUrl="x.jpg"
        naturalWidth={400}
        naturalHeight={400}
        quad={{
          tl: { x: 10, y: 10 },
          tr: { x: 10, y: 10 },
          br: { x: 10, y: 10 },
          bl: { x: 10, y: 10 },
        }}
      />,
    );
    expect(
      container.querySelector(".figure-crop-preview-empty"),
    ).not.toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
});
