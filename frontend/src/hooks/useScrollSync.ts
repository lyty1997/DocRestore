/**
 * 左右分栏同步滚动 hook。
 *
 * 约定：左右两个容器里，各自的"锚点元素"用 `data-page="<filename>"` 对齐，
 * 同 filename 表示视觉上应该对齐的位置。
 *
 * 工作流：
 * 1. 监听两侧 `scroll` 事件（rAF 节流）
 * 2. 被滚动侧：找 "距离容器视口中心最近的锚点" → 取其 `data-page`
 * 3. 对侧：找 `data-page === X` 的锚点，滚到它对齐容器视口中心
 * 4. 程序化滚动期间设 `isSyncing=true` 跳过对侧的 scroll 事件，防递归
 *
 * 设计决策：
 * - instant 对齐（非 smooth），跟手无延迟
 * - 用 data-page 属性而不是 index，健壮性高（图片重排/过滤不会错位）
 * - 找"最近中心"用单次 O(n) 扫描，不用 IntersectionObserver（节点数有限，
 *   且 IO 在容器 scroll 里配置 root 有兼容坑）
 */

import { useEffect, useRef, type RefObject } from "react";

export interface ScrollSyncOptions {
  /** 对齐方式：center（居中）/ start（顶部） */
  readonly align?: "center" | "start";
  /** 是否启用（关闭时不绑定 scroll 事件，供 edit 模式禁用） */
  readonly enabled?: boolean;
}

/**
 * 绑定左右两侧容器的同步滚动。
 *
 * 两侧容器内须有 `[data-page]` 锚点元素，且可滚动（overflow 非 visible）。
 */
export function useScrollSync(
  leftRef: RefObject<HTMLElement | null>,
  rightRef: RefObject<HTMLElement | null>,
  options: ScrollSyncOptions = {},
): void {
  const { align = "center", enabled = true } = options;

  //  程序化滚动时置 true，忽略被动触发的 scroll 事件，防止循环
  const isSyncingRef = useRef(false);
  const syncResetTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!enabled) return;

    const left = leftRef.current;
    const right = rightRef.current;
    if (!left || !right) return;

    const markProgrammatic = (): void => {
      isSyncingRef.current = true;
      if (syncResetTimerRef.current !== undefined) {
        globalThis.clearTimeout(syncResetTimerRef.current);
      }
      // 150ms 窗口覆盖 instant 滚动触发的异步 scroll event
      syncResetTimerRef.current = globalThis.setTimeout(() => {
        isSyncingRef.current = false;
        syncResetTimerRef.current = undefined;
      }, 150);
    };

    const makeHandler = (
      source: HTMLElement,
      target: HTMLElement,
    ): (() => void) => {
      let rafId: number | undefined;
      return () => {
        if (isSyncingRef.current) return;
        if (rafId !== undefined) return;
        rafId = globalThis.requestAnimationFrame(() => {
          rafId = undefined;
          const key = findActivePageKey(source);
          if (key === undefined) return;
          const targetEl = target.querySelector<HTMLElement>(
            `[data-page="${cssEscape(key)}"]`,
          );
          if (targetEl === null) return;
          markProgrammatic();
          scrollElementIntoContainer(target, targetEl, align);
        });
      };
    };

    const onLeftScroll = makeHandler(left, right);
    const onRightScroll = makeHandler(right, left);

    left.addEventListener("scroll", onLeftScroll, { passive: true });
    right.addEventListener("scroll", onRightScroll, { passive: true });

    return (): void => {
      left.removeEventListener("scroll", onLeftScroll);
      right.removeEventListener("scroll", onRightScroll);
      if (syncResetTimerRef.current !== undefined) {
        globalThis.clearTimeout(syncResetTimerRef.current);
        syncResetTimerRef.current = undefined;
      }
    };
  }, [leftRef, rightRef, align, enabled]);
}

/**
 * 扫描容器内所有 [data-page] 锚点，返回距离容器视口中心最近的那个的 key。
 */
function findActivePageKey(container: HTMLElement): string | undefined {
  const anchors = container.querySelectorAll<HTMLElement>("[data-page]");
  if (anchors.length === 0) return undefined;

  const containerRect = container.getBoundingClientRect();
  const viewportCenter = containerRect.top + containerRect.height / 2;

  let bestKey: string | undefined;
  let bestDist = Number.POSITIVE_INFINITY;
  for (const el of anchors) {
    const rect = el.getBoundingClientRect();
    const elCenter = rect.top + rect.height / 2;
    const dist = Math.abs(elCenter - viewportCenter);
    if (dist < bestDist) {
      bestDist = dist;
      bestKey = el.dataset.page;
    }
  }
  return bestKey;
}

/**
 * 把 child 滚到相对 container 视口的 align 位置（center/start）。
 *
 * 用 `container.scrollTop` 而不是 `scrollIntoView`：后者会滚动最近可滚动的祖先，
 * 可能把整个页面也滚起来；手动计算只动 container 自己。
 */
function scrollElementIntoContainer(
  container: HTMLElement,
  child: HTMLElement,
  align: "center" | "start",
): void {
  const containerRect = container.getBoundingClientRect();
  const childRect = child.getBoundingClientRect();
  // child 在 container 内的偏移（相对 container 内坐标系）
  const offsetInContainer =
    childRect.top - containerRect.top + container.scrollTop;

  const targetTop =
    align === "center"
      ? offsetInContainer - container.clientHeight / 2 + childRect.height / 2
      : offsetInContainer;

  container.scrollTop = Math.max(
    0,
    Math.min(
      targetTop,
      container.scrollHeight - container.clientHeight,
    ),
  );
}

/**
 * CSS.escape 的兼容 polyfill：给 querySelector 的属性值做转义。
 * filename 里可能含点、方括号等 CSS 选择器特殊字符。
 */
function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  // Fallback: 只处理最常见的几种；正式运行环境（现代浏览器）都走 CSS.escape
  return value.replaceAll(/["\\\n]/g, (c) => `\\${c}`);
}
