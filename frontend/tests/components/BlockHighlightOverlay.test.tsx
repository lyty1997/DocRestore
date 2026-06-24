import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BlockHighlightOverlay } from "../../src/components/BlockHighlightOverlay";

afterEach(cleanup);

describe("BlockHighlightOverlay", () => {
  it("bbox 按 image_size 换算成百分比矩形", () => {
    const { container } = render(
      <BlockHighlightOverlay bbox={[200, 0, 800, 800]} imageSize={[1000, 800]} />,
    );
    const box = container.querySelector<HTMLElement>(".block-highlight-overlay");
    expect(box).not.toBeNull();
    expect(box?.style.left).toBe("20%"); // 200 / 1000
    expect(box?.style.top).toBe("0%");
    expect(box?.style.width).toBe("60%"); // (800-200) / 1000
    expect(box?.style.height).toBe("100%"); // (800-0) / 800
  });

  it("非整张：偏移块换算正确", () => {
    const { container } = render(
      <BlockHighlightOverlay
        bbox={[120, 260, 2900, 980]}
        imageSize={[3000, 4000]}
      />,
    );
    const box = container.querySelector<HTMLElement>(".block-highlight-overlay");
    expect(box?.style.left).toBe("4%"); // 120 / 3000
    expect(box?.style.top).toBe("6.5%"); // 260 / 4000
  });

  it("image_size 非法（<=0）→ 不渲染（避免除零）", () => {
    const { container } = render(
      <BlockHighlightOverlay bbox={[0, 0, 10, 10]} imageSize={[0, 0]} />,
    );
    expect(
      container.querySelector(".block-highlight-overlay"),
    ).toBeNull();
  });
});
