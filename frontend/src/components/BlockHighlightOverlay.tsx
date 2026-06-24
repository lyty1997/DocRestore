/**
 * 版面块高亮矩形（Epic E · E3，设计 §6.4）。
 *
 * 叠在源图 ``<img>`` 所在相对定位容器上，按 bbox / image_size 换算成百分比矩形
 * （复用 CropEditor 同款 bbox→% 思路）。分母用 payload 的 ``image_size``（OCR 图
 * 像素），不依赖 ``<img>`` 实际解码尺寸，避免图片 decode race。失配时上层不渲染本组件。
 */

import type React from "react";

interface BlockHighlightOverlayProps {
  /** 原图像素 bbox ``(x0, y0, x1, y1)``。 */
  readonly bbox: readonly [number, number, number, number];
  /** OCR 图像素尺寸 ``(width, height)``，作百分比换算分母。 */
  readonly imageSize: readonly [number, number];
}

export function BlockHighlightOverlay({
  bbox,
  imageSize,
}: BlockHighlightOverlayProps): React.JSX.Element | undefined {
  const [w, h] = imageSize;
  if (w <= 0 || h <= 0) return undefined;
  const [x0, y0, x1, y1] = bbox;
  const left = (x0 / w) * 100;
  const top = (y0 / h) * 100;
  const width = ((x1 - x0) / w) * 100;
  const height = ((y1 - y0) / h) * 100;
  return (
    <div
      className="block-highlight-overlay"
      style={{
        left: `${left.toString()}%`,
        top: `${top.toString()}%`,
        width: `${width.toString()}%`,
        height: `${height.toString()}%`,
      }}
      aria-hidden
    />
  );
}
