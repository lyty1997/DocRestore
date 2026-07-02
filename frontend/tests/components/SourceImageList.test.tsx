import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { LayoutPagePayload } from "../../src/api/schemas";
import { SourceImageList } from "../../src/components/SourceImageList";
import type { SourceImageListItem } from "../../src/features/task/sourceImagePreview";
import { LanguageProvider } from "../../src/i18n";

afterEach(cleanup);

const IMAGES: readonly SourceImageListItem[] = [
  { name: "IMG_0001.jpg", pageKey: "IMG_0001.jpg" },
  { name: "IMG_0002.jpg", pageKey: "IMG_0002.jpg" },
];

function renderList(
  highlight?: Parameters<typeof SourceImageList>[0]["highlight"],
): HTMLElement {
  const { container } = render(
    <LanguageProvider>
      <SourceImageList
        taskId="t1"
        images={IMAGES}
        listClassName="source-images-list"
        imageClassName="source-image-item"
        highlight={highlight}
      />
    </LanguageProvider>,
  );
  return container;
}

describe("SourceImageList bbox 高亮", () => {
  it("无 highlight → 不渲染任何 overlay", () => {
    const container = renderList();
    expect(
      container.querySelectorAll(".block-highlight-overlay"),
    ).toHaveLength(0);
  });

  it("命中页只在对应 data-page 的图上叠一个 overlay", () => {
    const container = renderList({
      pageKey: "IMG_0001.jpg",
      bbox: [0, 0, 100, 50],
      imageSize: [800, 600],
    });
    const overlays = container.querySelectorAll(".block-highlight-overlay");
    expect(overlays).toHaveLength(1);

    const hitCell = container.querySelector<HTMLElement>(
      '[data-page="IMG_0001.jpg"]',
    );
    const otherCell = container.querySelector<HTMLElement>(
      '[data-page="IMG_0002.jpg"]',
    );
    expect(hitCell?.querySelector(".block-highlight-overlay")).not.toBeNull();
    expect(otherCell?.querySelector(".block-highlight-overlay")).toBeNull();
  });

  it("highlight 指向不存在的页 → 无 overlay", () => {
    const container = renderList({
      pageKey: "GHOST.jpg",
      bbox: [0, 0, 10, 10],
      imageSize: [800, 600],
    });
    expect(
      container.querySelectorAll(".block-highlight-overlay"),
    ).toHaveLength(0);
  });
});

function renderProcessed(processed: boolean): HTMLElement {
  const { container } = render(
    <LanguageProvider>
      <SourceImageList
        taskId="t1"
        images={IMAGES}
        listClassName="source-images-list"
        imageClassName="source-image-item"
        processed={processed}
      />
    </LanguageProvider>,
  );
  return container;
}

describe("SourceImageList 处理图（§13/§15）", () => {
  it("processed=false → img 显原图 source-images 端点", () => {
    const img = renderProcessed(false).querySelector("img");
    expect(img?.getAttribute("src")).toContain(
      "/tasks/t1/source-images/IMG_0001.jpg",
    );
  });

  it("processed=true → img 显处理图 processed-image 端点（按 pageKey 取）", () => {
    const src =
      renderProcessed(true).querySelector("img")?.getAttribute("src") ?? "";
    expect(src).toContain("/tasks/t1/processed-image");
    expect(src).toContain("name=IMG_0001.jpg");
  });
});

// ── E8：版面全览叠加层 ────────────────────────────────────────────

const LAYOUT_PAGES: readonly LayoutPagePayload[] = [
  {
    filename: "IMG_0001.jpg",
    image_size: [800, 600],
    blocks: [
      { bbox: [0, 0, 100, 50], label: "paragraph_title", index: 0, text: "标题", image_ref: "" },
      { bbox: [0, 60, 100, 200], label: "text", index: 1, text: "正文", image_ref: "" },
    ],
  },
  // IMG_0002.jpg 无版面页 → 该图不叠全览
];

function renderOverlay(showOverlay: boolean): HTMLElement {
  const { container } = render(
    <LanguageProvider>
      <SourceImageList
        taskId="t1"
        images={IMAGES}
        listClassName="source-images-list"
        imageClassName="source-image-item"
        layoutPages={LAYOUT_PAGES}
        showOverlay={showOverlay}
      />
    </LanguageProvider>,
  );
  return container;
}

describe("SourceImageList 版面全览（E8）", () => {
  it("showOverlay=false → 不渲染任何全览框", () => {
    expect(
      renderOverlay(false).querySelectorAll(".layout-overlay-box"),
    ).toHaveLength(0);
  });

  it("showOverlay=true → 仅有版面页的图叠该页全部块（按 pageKey 对齐 filename）", () => {
    const container = renderOverlay(true);
    // 该页 2 块 → 2 个全览框
    expect(
      container.querySelectorAll(".layout-overlay-box"),
    ).toHaveLength(2);
    const hitCell = container.querySelector<HTMLElement>(
      '[data-page="IMG_0001.jpg"]',
    );
    const otherCell = container.querySelector<HTMLElement>(
      '[data-page="IMG_0002.jpg"]',
    );
    expect(
      hitCell?.querySelectorAll(".layout-overlay-box"),
    ).toHaveLength(2);
    // 无版面页的图不叠框
    expect(
      otherCell?.querySelectorAll(".layout-overlay-box"),
    ).toHaveLength(0);
  });
});
