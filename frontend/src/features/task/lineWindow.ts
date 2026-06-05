/**
 * 行级虚拟化的可视窗口计算（纯函数，便于单测）。
 *
 * 代码模式只读视图把整文件按固定行高虚拟化：只渲染 [start, end) 区间的行，
 * 上下用 spacer 占位。该函数据滚动位置 / 视口高度 / 行高算出应渲染的行区间，
 * 两端各留 overscan 行缓冲，避免快速滚动时露白。
 */

export interface LineWindow {
  /** 起始行下标（含） */
  readonly start: number;
  /** 结束行下标（不含） */
  readonly end: number;
}

/**
 * 计算应渲染的行窗口。
 *
 * @param scrollTop  容器当前滚动距离（px）
 * @param viewportH  容器可视高度（px）
 * @param rowH       单行高度（px，须 > 0）
 * @param totalLines 总行数
 * @param overscan   视口上下各额外渲染的缓冲行数
 */
export function computeLineWindow(
  scrollTop: number,
  viewportH: number,
  rowH: number,
  totalLines: number,
  overscan: number,
): LineWindow {
  // 退化输入直接整文件渲染，避免除零 / 负区间。
  if (rowH <= 0 || totalLines <= 0) {
    return { start: 0, end: Math.max(0, totalLines) };
  }
  const safeScroll = Math.max(0, scrollTop);
  const safeViewport = Math.max(0, viewportH);
  const buffer = Math.max(0, Math.floor(overscan));

  const start = Math.max(0, Math.floor(safeScroll / rowH) - buffer);
  const end = Math.min(
    totalLines,
    Math.ceil((safeScroll + safeViewport) / rowH) + buffer,
  );
  // end 至少不小于 start，避免视口高度为 0 时算出空区间又被 spacer 撑开。
  return { start, end: Math.max(start, end) };
}
