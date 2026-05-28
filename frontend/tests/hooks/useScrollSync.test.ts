/**
 * useScrollSync：左右分栏同步滚动 hook 的集成测试。
 *
 * 用 jsdom 构造两个可滚动 container + 各自若干带 data-page 的锚点，
 * 触发 scroll 事件后验证对侧 scrollTop 会被对齐到同 key 锚点的位置。
 *
 * 主要不变量：
 * 1. 左滚 → 右同步到同 data-page 锚点居中
 * 2. 右滚 → 左同步到同 data-page 锚点居中
 * 3. 防递归：程序化滚动不触发反向再同步
 * 4. enabled=false 时不绑定事件（edit 模式禁用）
 * 5. continuous 模式按视口中心在 page 区间内的比例连续映射
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useScrollSync } from "../../src/hooks/useScrollSync";

/**
 * 给 jsdom 里的元素补上 getBoundingClientRect / clientHeight / scrollHeight
 * 等布局属性，让滚动同步 hook 能算出位置。
 */
function makeContainer(
  anchors: readonly {
    readonly key: string;
    readonly top: number;
    readonly height?: number;
  }[],
  opts: {
    readonly containerTop?: number;
    readonly viewportHeight?: number;
    readonly scrollHeight?: number;
  } = {},
): { container: HTMLDivElement; anchorEls: HTMLElement[] } {
  const containerTop = opts.containerTop ?? 0;
  const viewportHeight = opts.viewportHeight ?? 400;
  const scrollHeight = opts.scrollHeight ?? 2000;
  const container = document.createElement("div");
  document.body.append(container);

  const anchorEls: HTMLElement[] = [];
  for (const { key, top, height = 0 } of anchors) {
    const el = document.createElement("span");
    el.dataset.page = key;
    container.append(el);
    // 虚拟锚点默认高度 0；图片锚点测试可显式传 height。
    Object.defineProperty(el, "getBoundingClientRect", {
      value: () => ({
        top: containerTop + top - container.scrollTop,
        bottom: containerTop + top + height - container.scrollTop,
        left: 0,
        right: 0,
        width: 0,
        height,
        x: 0,
        y: containerTop + top - container.scrollTop,
        toJSON: () => ({}),
      }),
      configurable: true,
    });
    anchorEls.push(el);
  }

  Object.defineProperty(container, "getBoundingClientRect", {
    value: () => ({
      top: containerTop,
      bottom: containerTop + viewportHeight,
      left: 0,
      right: 0,
      width: 500,
      height: viewportHeight,
      x: 0,
      y: containerTop,
      toJSON: () => ({}),
    }),
    configurable: true,
  });

  Object.defineProperty(container, "clientHeight", {
    value: viewportHeight,
    configurable: true,
  });
  Object.defineProperty(container, "scrollHeight", {
    value: scrollHeight,
    configurable: true,
  });

  return { container, anchorEls };
}

/** 模拟用户滚动容器：改 scrollTop + dispatch scroll 事件。 */
function simulateScroll(container: HTMLElement, scrollTop: number): void {
  container.scrollTop = scrollTop;
  container.dispatchEvent(new Event("scroll"));
}

/** rAF flush：推进一帧以让 useScrollSync 的 requestAnimationFrame 回调执行。 */
async function flushRaf(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => { resolve(); });
    });
  });
}

describe("useScrollSync", () => {
  let cleanup: (() => void)[] = [];

  beforeEach(() => {
    cleanup = [];
    // jsdom 没原生 requestAnimationFrame，用 setTimeout 兜底
    if (typeof globalThis.requestAnimationFrame !== "function") {
      globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) =>
        setTimeout(() => { cb(performance.now()); }, 16)) as typeof requestAnimationFrame;
    }
  });

  afterEach(() => {
    for (const fn of cleanup) fn();
    document.body.innerHTML = "";
  });

  it("左滚动 → 右侧对齐到同 data-page 锚点中心", async () => {
    const left = makeContainer([
      { key: "a.jpg", top: 50 },
      { key: "b.jpg", top: 600 },
      { key: "c.jpg", top: 1200 },
    ]);
    const right = makeContainer([
      { key: "a.jpg", top: 100 },
      { key: "b.jpg", top: 800 },
      { key: "c.jpg", top: 1500 },
    ]);


    renderHook(() => { useScrollSync(left.container, right.container); });

    // 模拟左侧滚到让 b.jpg (top=600) 接近视口中心（viewport=400，中心 200）
    // 滚 500 后 b 在视口内坐标 = 600 - 500 = 100，居中心 200 最近的是 b
    simulateScroll(left.container, 500);
    await flushRaf();

    // 右侧 b.jpg top=800，viewport=400，居中时 scrollTop = 800 - 200 = 600
    expect(right.container.scrollTop).toBe(600);
  });

  it("右滚动 → 左侧对齐", async () => {
    const left = makeContainer([
      { key: "a.jpg", top: 50 },
      { key: "b.jpg", top: 500 },
    ]);
    const right = makeContainer([
      { key: "a.jpg", top: 100 },
      { key: "b.jpg", top: 900 },
    ]);


    renderHook(() => { useScrollSync(left.container, right.container); });

    // 右滚 700，b.jpg 在视口坐标 = 900 - 700 = 200，正好居中
    simulateScroll(right.container, 700);
    await flushRaf();

    // 左侧 b.jpg top=500，居中 scrollTop = 500 - 200 = 300
    expect(left.container.scrollTop).toBe(300);
  });

  it("enabled=false 时不同步", async () => {
    const left = makeContainer([{ key: "a.jpg", top: 0 }]);
    const right = makeContainer([{ key: "a.jpg", top: 500 }]);


    renderHook(() => { useScrollSync(left.container, right.container, { enabled: false }); });

    simulateScroll(left.container, 200);
    await flushRaf();

    expect(right.container.scrollTop).toBe(0);
  });

  it("程序化滚动不引起反向再同步（防递归）", async () => {
    const left = makeContainer([
      { key: "a.jpg", top: 0 },
      { key: "b.jpg", top: 500 },
    ]);
    const right = makeContainer([
      { key: "a.jpg", top: 0 },
      { key: "b.jpg", top: 900 },
    ]);


    renderHook(() => { useScrollSync(left.container, right.container); });

    // 左滚触发右侧程序化滚动
    simulateScroll(left.container, 400);
    await flushRaf();
    const rightAfterFirst = right.container.scrollTop;

    // 右侧收到的"程序化 scroll"事件不应反过来改左侧
    const leftBefore = left.container.scrollTop;
    right.container.dispatchEvent(new Event("scroll"));
    await flushRaf();

    expect(left.container.scrollTop).toBe(leftBefore);
    expect(right.container.scrollTop).toBe(rightAfterFirst);
  });

  it("对侧找不到同 key 锚点时不动", async () => {
    const left = makeContainer([{ key: "a.jpg", top: 0 }]);
    const right = makeContainer([{ key: "different.jpg", top: 300 }]);


    renderHook(() => { useScrollSync(left.container, right.container); });

    simulateScroll(left.container, 50);
    await flushRaf();

    expect(right.container.scrollTop).toBe(0);
  });

  it("align=start 模式：选最后一个穿过顶部阈值的锚点（非几何中心）", async () => {
    // 场景：左侧是图片缩略图（anchors 有高度），右侧是长 markdown（anchors
    // 零高度）。滚到"10.jpg 的图恰好在视口顶部可见"时，应对齐右侧 10.jpg
    // 段落的开头，而不是因为图片本身高度让中心点偏到 11.jpg。
    const left = makeContainer(
      [
        { key: "1.jpg", top: 0 },
        { key: "10.jpg", top: 160 },
        { key: "11.jpg", top: 310 },
      ],
      { scrollHeight: 600 },
    );
    const right = makeContainer(
      [
        { key: "1.jpg", top: 20 },
        { key: "10.jpg", top: 1680 },
        { key: "11.jpg", top: 3360 },
      ],
      { scrollHeight: 4000 },
    );

    renderHook(() => {
      useScrollSync(left.container, right.container, { align: "start" });
    });

    // left scrollTop = 160 —— 10.jpg 锚点在视口顶部，应对齐 right 的 10.jpg
    simulateScroll(left.container, 160);
    await flushRaf();

    // align=start → right.scrollTop 应为 1680（不是中心对齐的其他值）
    expect(right.container.scrollTop).toBe(1680);
  });

  it("continuous 模式：按源侧视口中心在 page 区间内的比例连续映射", async () => {
    const text = makeContainer(
      [
        { key: "1.jpg", top: 0 },
        { key: "2.jpg", top: 1000 },
      ],
      { viewportHeight: 400, scrollHeight: 2400 },
    );
    const images = makeContainer(
      [
        { key: "1.jpg", top: 0, height: 800 },
        { key: "2.jpg", top: 900, height: 800 },
      ],
      { viewportHeight: 400, scrollHeight: 2000 },
    );

    renderHook(() => {
      useScrollSync(text.container, images.container, { align: "continuous" });
    });

    // 文本滚到 centerY = 500，处在 1.jpg → 2.jpg 区间 50%。
    // 原图同区间为 0 → 900，映射点 450 居中后 scrollTop = 250。
    simulateScroll(text.container, 300);
    await flushRaf();

    expect(images.container.scrollTop).toBe(250);
  });

  it("continuous 模式：源侧连续滚动不会被程序化同步标记吞掉", async () => {
    const text = makeContainer(
      [
        { key: "1.jpg", top: 0 },
        { key: "2.jpg", top: 1000 },
      ],
      { viewportHeight: 400, scrollHeight: 2400 },
    );
    const images = makeContainer(
      [
        { key: "1.jpg", top: 0, height: 800 },
        { key: "2.jpg", top: 900, height: 800 },
      ],
      { viewportHeight: 400, scrollHeight: 2000 },
    );

    renderHook(() => {
      useScrollSync(text.container, images.container, { align: "continuous" });
    });

    simulateScroll(text.container, 300);
    await flushRaf();
    expect(images.container.scrollTop).toBe(250);

    // 未等待 150ms 程序化标记过期，继续滚源侧；新的同步仍应生效。
    simulateScroll(text.container, 500);
    await flushRaf();

    // centerY = 700，比例 70%；目标点 630，居中后 scrollTop = 430。
    expect(images.container.scrollTop).toBe(430);
  });

  it("continuous 模式：最后一页没有下一锚点时仍按剩余内容连续映射", async () => {
    const text = makeContainer(
      [
        { key: "1.jpg", top: 0 },
        { key: "2.jpg", top: 1000 },
      ],
      { viewportHeight: 400, scrollHeight: 2400 },
    );
    const images = makeContainer(
      [
        { key: "1.jpg", top: 0, height: 800 },
        { key: "2.jpg", top: 900, height: 800 },
      ],
      { viewportHeight: 400, scrollHeight: 2000 },
    );

    renderHook(() => {
      useScrollSync(text.container, images.container, { align: "continuous" });
    });

    // centerY = 1500，处在最后一页剩余区间 1000 → 2400 的 5/14。
    simulateScroll(text.container, 1300);
    await flushRaf();

    // 目标最后一张图高度 800，映射点为 900 + 800 * 5/14，再居中。
    expect(images.container.scrollTop).toBeCloseTo(985.714, 3);
  });

  it("卸载时移除事件监听（不泄漏）", async () => {
    const left = makeContainer([{ key: "a.jpg", top: 0 }]);
    const right = makeContainer([{ key: "a.jpg", top: 500 }]);


    const { unmount } = renderHook(() => { useScrollSync(left.container, right.container); });
    unmount();

    // 卸载后滚动不应再触发同步
    simulateScroll(left.container, 300);
    await flushRaf();
    expect(right.container.scrollTop).toBe(0);
  });
});
