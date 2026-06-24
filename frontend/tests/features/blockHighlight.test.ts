import { describe, expect, it } from "vitest";

import type { LayoutPayload } from "../../src/api/schemas";
import {
  computeBlockHighlight,
  type CursorBlock,
} from "../../src/features/task/blockHighlight";

const LAYOUT: LayoutPayload = {
  pages: [
    {
      filename: "IMG_0001.jpg",
      image_size: [3024, 4032],
      blocks: [
        { bbox: [120, 88, 2900, 240], label: "paragraph_title", text: "第一章" },
        { bbox: [120, 260, 2900, 980], label: "text", text: "本文研究OCR还原" },
      ],
    },
    {
      filename: "IMG_0002.jpg",
      image_size: [3024, 4032],
      blocks: [{ bbox: [0, 0, 100, 50], label: "text", text: "第二页正文" }],
    },
  ],
};

describe("computeBlockHighlight", () => {
  it("layout 缺省 → undefined", () => {
    const cursor: CursorBlock = { page: "IMG_0001.jpg", text: "第一章" };
    expect(computeBlockHighlight(undefined, cursor)).toBeUndefined();
  });

  it("cursorBlock 缺省 → undefined", () => {
    const noCursor: CursorBlock | undefined = undefined;
    expect(computeBlockHighlight(LAYOUT, noCursor)).toBeUndefined();
  });

  it("光标所在页不在 layout → undefined", () => {
    const cursor: CursorBlock = { page: "GHOST.jpg", text: "第一章" };
    expect(computeBlockHighlight(LAYOUT, cursor)).toBeUndefined();
  });

  it("命中 → 该页 bbox + image_size + pageKey", () => {
    const cursor: CursorBlock = { page: "IMG_0001.jpg", text: "本文研究 OCR 还原" };
    const hit = computeBlockHighlight(LAYOUT, cursor);
    expect(hit).toEqual({
      pageKey: "IMG_0001.jpg",
      bbox: [120, 260, 2900, 980],
      imageSize: [3024, 4032],
    });
  });

  it("命中页正确：第二页文字定位到第二页 bbox（不串页）", () => {
    const cursor: CursorBlock = { page: "IMG_0002.jpg", text: "第二页正文" };
    const hit = computeBlockHighlight(LAYOUT, cursor);
    expect(hit?.pageKey).toBe("IMG_0002.jpg");
    expect(hit?.bbox).toEqual([0, 0, 100, 50]);
  });

  it("该页内无相近块 → undefined", () => {
    const cursor: CursorBlock = { page: "IMG_0001.jpg", text: "毫不相干的内容" };
    expect(computeBlockHighlight(LAYOUT, cursor)).toBeUndefined();
  });
});
