/**
 * 只读预览侧光标块检测 ``previewBlockAtPointer``（Epic E · E4，#88）。
 *
 * 构造与 react-markdown + ``injectPageAnchors`` 等价的预览 DOM（``.markdown-preview``
 * 容器，直接子节点 = 页锚点 span + 段落/标题/表格块），从不同元素「hover」断言
 * 返回「最近前置页 + 该块文字」，并覆盖容器外/空块/无前置页等退化路径。镜像
 * ``blockAtCursor.test.ts`` 的语义对照。
 */

import { afterEach, describe, expect, it } from "vitest";

import { previewBlockAtPointer } from "../../src/features/task/previewBlockAtPointer";

let container: HTMLDivElement | undefined;

afterEach(() => {
  container?.remove();
  container = undefined;
});

/** 把 HTML 渲染进挂载到 document 的 ``.markdown-preview`` 容器，返回容器。 */
function mount(html: string): HTMLDivElement {
  const el = document.createElement("div");
  el.className = "markdown-preview";
  el.innerHTML = html;
  document.body.append(el);
  container = el;
  return el;
}

function anchor(page: string): string {
  return `<span class="page-anchor" data-page="${page}"></span>`;
}

/** 预览 DOM：页锚点 span 与内容块交替，均为容器直接子节点。 */
const CONTENT =
  anchor("IMG_0001.jpg") +
  "<p>第一页的正文段落</p>" +
  anchor("IMG_0002.jpg") +
  "<h2>第二页的<strong>小标题</strong></h2>" +
  "<p>第二页的正文段落</p>";

describe("previewBlockAtPointer", () => {
  it("hover 第一段 → 第一页 + 该段文字", () => {
    const el = mount(CONTENT);
    const target = el.querySelector("p");
    expect(previewBlockAtPointer(target, el)).toEqual({
      page: "IMG_0001.jpg",
      text: "第一页的正文段落",
    });
  });

  it("hover 块内子元素 → 向上取容器直接子块（含整块文字）", () => {
    const el = mount(CONTENT);
    const inner = el.querySelector("h2 strong");
    expect(previewBlockAtPointer(inner, el)).toEqual({
      page: "IMG_0002.jpg",
      text: "第二页的小标题",
    });
  });

  it("hover 第二页正文 → 取最近前置页（第二页，非第一页）", () => {
    const el = mount(CONTENT);
    const target = [...el.querySelectorAll("p")].at(-1);
    expect(previewBlockAtPointer(target, el)).toEqual({
      page: "IMG_0002.jpg",
      text: "第二页的正文段落",
    });
  });

  it("块在首个页锚点之前（无前置页）→ undefined", () => {
    const el = mount("<p>无页标记的段落</p>" + anchor("X.jpg"));
    expect(previewBlockAtPointer(el.querySelector("p"), el)).toBeUndefined();
  });

  it("空白块 → undefined（与编辑器空块退化一致）", () => {
    const el = mount(anchor("IMG_0001.jpg") + "<p>   </p>");
    expect(previewBlockAtPointer(el.querySelector("p"), el)).toBeUndefined();
  });

  it("target 为容器自身（hover 内边距）→ undefined", () => {
    const el = mount(CONTENT);
    expect(previewBlockAtPointer(el, el)).toBeUndefined();
  });

  it("target 在容器外 → undefined", () => {
    const el = mount(CONTENT);
    const outside = document.createElement("p");
    outside.textContent = "容器外段落";
    document.body.append(outside);
    try {
      expect(previewBlockAtPointer(outside, el)).toBeUndefined();
    } finally {
      outside.remove();
    }
  });

  it("target 为 null（querySelector 落空）→ undefined", () => {
    const el = mount(CONTENT);
    // querySelector 落空返回真实 null，等价于 e.target 为 null 的运行时路径
    expect(previewBlockAtPointer(el.querySelector(".missing"), el))
      .toBeUndefined();
  });

  it("页锚点被包在 <p> 里（react-markdown 包裹形态）仍能求页", () => {
    const el = mount(
      `<p>${anchor("IMG_0009.jpg")}</p>` + "<p>包裹形态下的正文</p>",
    );
    const target = [...el.querySelectorAll("p")].at(-1);
    expect(previewBlockAtPointer(target, el)).toEqual({
      page: "IMG_0009.jpg",
      text: "包裹形态下的正文",
    });
  });
});
