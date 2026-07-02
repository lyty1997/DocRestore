/**
 * 版面全览叠加层（Epic E · E8/#92，设计 §17）。
 *
 * 把整页**全部**版面块按 bbox 画成彩色分类框（``label`` → 色）+ 左上角阅读序角标
 * （``index+1``），铺在源图上作可开关的「版面全览」，达 mineru.net 版面图效果。
 * 复用 {@link BlockHighlightOverlay} 同款 bbox→% 换算（同分母 ``image_size``，不依赖
 * ``<img>`` 解码尺寸避 decode race）。与单块橙色高亮共存：全览框在下、命中橙框在上。
 * 纯展示、``aria-hidden``。``image_size`` 非法 → 不渲染（上层自然无框）。
 */

import type React from "react";

import type { LayoutBlockPayload } from "../api/schemas";
import { bboxToPercentRect } from "../features/task/bboxRect";
import { categoryColor } from "../features/task/layoutCategory";

interface LayoutOverlayProps {
  /** 该页全部版面块（含 bbox / label / 阅读序 index）。 */
  readonly blocks: readonly LayoutBlockPayload[];
  /** OCR 图像素尺寸 ``(width, height)``，作百分比换算分母（与单块高亮同源）。 */
  readonly imageSize: readonly [number, number];
}

export function LayoutOverlay({
  blocks,
  imageSize,
}: LayoutOverlayProps): React.JSX.Element | undefined {
  const [w, h] = imageSize;
  if (w <= 0 || h <= 0) return undefined;
  return (
    <>
      {blocks.map((block) => {
        const { left, top, width, height } = bboxToPercentRect(block.bbox, w, h);
        const color = categoryColor(block.label);
        return (
          <div
            // key 用阅读序 index（稳定、唯一），叠 label 防同序号异常数据撞键。
            key={`${block.index.toString()}-${block.label}`}
            className="layout-overlay-box"
            style={{
              left: `${left.toString()}%`,
              top: `${top.toString()}%`,
              width: `${width.toString()}%`,
              height: `${height.toString()}%`,
              borderColor: color.border,
              backgroundColor: color.fill,
            }}
            aria-hidden
          >
            <span
              className="layout-overlay-badge"
              style={{ backgroundColor: color.border }}
            >
              {block.index + 1}
            </span>
          </div>
        );
      })}
    </>
  );
}
