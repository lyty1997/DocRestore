import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { LayoutBlockPayload } from "../../src/api/schemas";
import { LayoutOverlay } from "../../src/components/LayoutOverlay";

afterEach(cleanup);

function block(
  index: number,
  label: string,
  bbox: [number, number, number, number],
): LayoutBlockPayload {
  return { bbox, label, index, text: "", image_ref: "" };
}

const BLOCKS: readonly LayoutBlockPayload[] = [
  block(0, "paragraph_title", [0, 0, 500, 100]),
  block(1, "text", [0, 120, 500, 400]),
  block(2, "image", [0, 420, 500, 800]),
];

describe("LayoutOverlay", () => {
  it("每块渲染一个彩色框 + 一个序号角标", () => {
    const { container } = render(
      <LayoutOverlay blocks={BLOCKS} imageSize={[500, 800]} />,
    );
    expect(container.querySelectorAll(".layout-overlay-box")).toHaveLength(3);
    expect(container.querySelectorAll(".layout-overlay-badge")).toHaveLength(3);
  });

  it("角标显 index+1（1-based 阅读序）", () => {
    const { container } = render(
      <LayoutOverlay blocks={BLOCKS} imageSize={[500, 800]} />,
    );
    const badges = [
      ...container.querySelectorAll(".layout-overlay-badge"),
    ].map((b) => b.textContent);
    expect(badges).toEqual(["1", "2", "3"]);
  });

  it("bbox 按 image_size 换算成百分比", () => {
    const { container } = render(
      <LayoutOverlay
        blocks={[block(0, "text", [100, 0, 400, 800])]}
        imageSize={[1000, 800]}
      />,
    );
    const box = container.querySelector<HTMLElement>(".layout-overlay-box");
    expect(box?.style.left).toBe("10%"); // 100 / 1000
    expect(box?.style.width).toBe("30%"); // (400-100) / 1000
  });

  it("不同类着不同边框色（均非空）", () => {
    const { container } = render(
      <LayoutOverlay
        blocks={[
          block(0, "table", [0, 0, 10, 10]),
          block(1, "image", [0, 20, 10, 30]),
        ]}
        imageSize={[100, 100]}
      />,
    );
    const boxes = [
      ...container.querySelectorAll<HTMLElement>(".layout-overlay-box"),
    ];
    expect(boxes[0]?.style.borderColor).not.toBe("");
    expect(boxes[0]?.style.borderColor).not.toBe(boxes[1]?.style.borderColor);
  });

  it("image_size 非法（<=0）→ 不渲染（避免除零）", () => {
    const { container } = render(
      <LayoutOverlay blocks={BLOCKS} imageSize={[0, 0]} />,
    );
    expect(container.querySelectorAll(".layout-overlay-box")).toHaveLength(0);
  });
});
