/**
 * 预览区同步滚动策略。
 *
 * 文档模式（Markdown ↔ 原图）和代码模式（代码 ↔ 原图）都使用 page
 * 锚点连续映射，保持两个模式的滚动手感一致。
 */

import { useScrollSync } from "./useScrollSync";

export function usePreviewScrollSync(
  source: HTMLElement | null | undefined,
  target: HTMLElement | null | undefined,
  enabled: boolean,
): void {
  useScrollSync(source, target, {
    align: "continuous",
    enabled,
  });
}
