/**
 * bbox → 百分比矩形换算（BlockHighlightOverlay 与 CropEditor 共用）。
 *
 * 把像素 bbox ``(x0, y0, x1, y1)`` 相对底图尺寸 ``(width, height)`` 换成百分比矩形
 * （left/top/width/height，单位 %），供叠在相对定位容器上定位。抽此避免两处逐字节
 * 重复的换算公式漂移。调用方须保证 ``width`` / ``height`` > 0（本函数不做除零守卫）。
 */

/** 百分比矩形（0..100，单位 %）。 */
export interface PercentRect {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

/** 像素 bbox → 相对 ``(width, height)`` 的百分比矩形。 */
export function bboxToPercentRect(
  bbox: readonly [number, number, number, number],
  width: number,
  height: number,
): PercentRect {
  const [x0, y0, x1, y1] = bbox;
  return {
    left: (x0 / width) * 100,
    top: (y0 / height) * 100,
    width: ((x1 - x0) / width) * 100,
    height: ((y1 - y0) / height) * 100,
  };
}
