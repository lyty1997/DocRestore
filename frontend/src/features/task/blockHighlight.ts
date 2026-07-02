/**
 * 光标块 → 原图 bbox 高亮的域模型与计算（Epic E · E3）。
 *
 * 这里集中 Epic E 跨组件共享的两个域类型（编辑器产出的 ``CursorBlock``、源图列表
 * 消费的 ``SourceImageHighlight``）与纯计算 ``computeBlockHighlight``，使
 * features 层不反向依赖 components；编辑器 / 源图列表 / 预览面板都从此处取类型。
 */

import type { LayoutPayload } from "../../api/schemas";
import { matchBlock } from "./blockMatch";

/** 编辑器光标所在顶层块（页 + 纯文本 / 图片引用），供匹配版面块。 */
export interface CursorBlock {
  /** 光标所在页（最近前置 pageAnchor 的原图基名）。 */
  readonly page: string;
  /** 光标所在顶层块纯文本（已被精修改写，用于模糊匹配 raw OCR 文字）。 */
  readonly text: string;
  /** 图片/图表块：`<img src>` 提取的 `images/xxx` 引用，按引用精确匹配版面图片块
   *  （图片块无文字，模糊匹配命中不了）；非图片块为 undefined。 */
  readonly imageRef?: string;
}

/** 从 `<img src>`（asset URL 或相对路径）提取 `images/xxx` 引用以匹配版面图片块。 */
export function extractImageRef(src: string): string | undefined {
  const path = src.split("?", 1)[0] ?? src;
  const idx = path.lastIndexOf("images/");
  return idx === -1 ? undefined : path.slice(idx);
}

/** 命中后用于在源图上画矩形的高亮（页 + 像素 bbox + 换算分母尺寸）。 */
export interface SourceImageHighlight {
  /** 命中页（原图基名，对齐 data-page / pageKey）。 */
  readonly pageKey: string;
  /** 原图像素 bbox ``(x0, y0, x1, y1)``。 */
  readonly bbox: readonly [number, number, number, number];
  /** OCR 图像素尺寸 ``(width, height)``，作百分比换算分母。 */
  readonly imageSize: readonly [number, number];
}

/**
 * 光标块 → 命中页 bbox 高亮；无 layout / 无该页 / 失配 → undefined（不高亮）。
 *
 * layout 的 ``filename`` 与 ``cursorBlock.page``（pageAnchor 基名）、源图
 * ``data-page`` 三者同为原图基名，按 filename 定位页后在该页候选块里模糊匹配。
 */
export function computeBlockHighlight(
  layout: LayoutPayload | undefined,
  cursorBlock: CursorBlock | undefined,
): SourceImageHighlight | undefined {
  if (layout === undefined || cursorBlock === undefined) return undefined;
  const page = layout.pages.find((p) => p.filename === cursorBlock.page);
  if (page === undefined) return undefined;
  // 图片块按 image_ref 精确匹配（无文字，模糊匹配命中不了）；否则文字模糊匹配。
  const ref = cursorBlock.imageRef;
  const matched =
    ref !== undefined && ref !== ""
      ? page.blocks.find((b) => b.image_ref !== "" && b.image_ref === ref)
      : matchBlock(page.blocks, cursorBlock.text);
  if (matched === undefined) return undefined;
  return {
    pageKey: cursorBlock.page,
    bbox: matched.bbox,
    imageSize: page.image_size,
  };
}
