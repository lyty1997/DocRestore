/**
 * 裁剪框 → 视口缩放联动的纯几何计算。
 *
 * 编辑器外包一层固定尺寸视口（overflow hidden）：内容层按"整图等比适配视口"
 * 的基准尺寸布局，再叠加 translate + scale，把裁剪框区域铺满视口约
 * ``FILL_RATIO``。松手时重新计算目标变换，由 CSS transition 平滑过渡——
 * 原图随裁剪框缩放、在窗口内铺开，无需独立预览窗。
 */

import type { CropQuad } from "../../api/schemas";

/** 裁剪框在视口里铺开的目标占比（留边方便继续往外扩框）。 */
const FILL_RATIO = 0.78;

/** 放大封顶：1 源图像素最多放大到 2 CSS 像素，避免过度模糊。 */
const MAX_PIXEL_SCALE = 2;

/** 源图像素坐标系下的外接矩形（CropBox 同构；quad 模式取四点外接框）。 */
export interface RegionBBox {
  readonly x0: number;
  readonly y0: number;
  readonly x1: number;
  readonly y1: number;
}

/** 视口内容层的目标变换（transform-origin 均为 0 0）。 */
export interface ViewTransform {
  /** 内容层布局宽度 = 整图等比适配视口后的 CSS 像素宽。 */
  readonly baseWidth: number;
  /** 内容层布局高度（与 baseWidth 同比例）。 */
  readonly baseHeight: number;
  /** 基准尺寸上的额外缩放（≥1；=1 即整图刚好适配视口）。 */
  readonly zoom: number;
  /** 内容层 X 平移（CSS 像素）。 */
  readonly tx: number;
  /** 内容层 Y 平移（CSS 像素）。 */
  readonly ty: number;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/** 四角校正模式的联动区域：取四个角点的外接矩形。 */
export function quadBBox(quad: CropQuad): RegionBBox {
  const xs = [quad.tl.x, quad.tr.x, quad.br.x, quad.bl.x];
  const ys = [quad.tl.y, quad.tr.y, quad.br.y, quad.bl.y];
  return {
    x0: Math.min(...xs),
    y0: Math.min(...ys),
    x1: Math.max(...xs),
    y1: Math.max(...ys),
  };
}

/**
 * 计算"裁剪框铺满视口"的内容层变换（框始终完整可见，取两方向最小缩放）。
 *
 * - zoom 下限 1（不比整图适配更小）、上限按 ``MAX_PIXEL_SCALE`` 封顶；
 * - 框中心对准视口中心，再夹取平移避免图边内侧露空（内容比视口小则居中）。
 * - 纵向整高框（正文裁剪 MVP）数学上 zoom 恒为 1：高度项 0.78×vh/(图高×s0)
 *   ≡ 0.78 被下限钳住——此时框纵向正好填满视口、水平居中压暗，**这是预期
 *   行为**。曾加 "width" 宽度主导模式强行放大，被用户否决（铺开过大，框的
 *   上下边整个溢出视口无法选中，过犹不及），勿重试。
 *
 * 视口或原图尺寸非正（如 jsdom 无布局）返回 ``undefined``，调用方回退为
 * 不变换的整图展示。
 */
export function fitRegion(
  viewportWidth: number,
  viewportHeight: number,
  naturalWidth: number,
  naturalHeight: number,
  region: RegionBBox,
): ViewTransform | undefined {
  if (viewportWidth <= 0 || viewportHeight <= 0) return undefined;
  if (naturalWidth <= 0 || naturalHeight <= 0) return undefined;
  // 整图等比适配视口的基准缩放（源图像素 → CSS 像素）
  const s0 = Math.min(
    viewportWidth / naturalWidth,
    viewportHeight / naturalHeight,
  );
  const baseWidth = naturalWidth * s0;
  const baseHeight = naturalHeight * s0;
  const regionW = Math.max(1, region.x1 - region.x0);
  const regionH = Math.max(1, region.y1 - region.y0);
  const zoomFit =
    FILL_RATIO
    * Math.min(
      viewportWidth / (regionW * s0),
      viewportHeight / (regionH * s0),
    );
  const zoomMax = Math.max(1, MAX_PIXEL_SCALE / s0);
  const zoom = clamp(zoomFit, 1, zoomMax);
  // 框中心在内容层（已含 zoom）坐标系的位置
  const contentW = baseWidth * zoom;
  const contentH = baseHeight * zoom;
  const centerX = ((region.x0 + region.x1) / 2) * s0 * zoom;
  const centerY = ((region.y0 + region.y1) / 2) * s0 * zoom;
  const tx =
    contentW <= viewportWidth
      ? (viewportWidth - contentW) / 2
      : clamp(viewportWidth / 2 - centerX, viewportWidth - contentW, 0);
  const ty =
    contentH <= viewportHeight
      ? (viewportHeight - contentH) / 2
      : clamp(viewportHeight / 2 - centerY, viewportHeight - contentH, 0);
  return { baseWidth, baseHeight, zoom, tx, ty };
}
