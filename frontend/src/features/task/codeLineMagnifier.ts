/**
 * 代码模式悬停行 → 原图局部放大的域模型与纯计算（#93 · F2）。
 *
 * 输入 ``getTaskCodeLayout`` 的载荷，建「path → (line_no → {page,bbox})」索引；
 * 给定当前行号求放大区域：当前行 ±1 的**同页** bbox 并集（跨页边界只并同页那侧），
 * 当前行无 bbox（推断行 / gap 占位）时向两侧就近取最近有 bbox 的行回退；都无 →
 * undefined（不放大，优于错放）。纯几何，无 DOM 依赖，便于单测。
 */

import type { CodeLayoutPayload } from "../../api/schemas";
import type { RegionBBox } from "./cropFit";

/** 原图像素 bbox ``(x0,y0,x1,y1)``（与 payload 同构）。 */
export type LineBoxTuple = readonly [number, number, number, number];

/** 单行命中：来源页标识 + 原图 bbox。 */
export interface LineBox {
  readonly page: string;
  readonly bbox: LineBoxTuple;
}

/** 单文件行索引：line_no → 命中。 */
export type FileLineIndex = Map<number, LineBox>;

/** 全任务行索引：path → 单文件行索引。 */
export type CodeLineIndex = Map<string, FileLineIndex>;

/** 单文件行映射（#5）：精修后行序(0-based) → 原 OCR line_no，null = 改写/新增行→不放大。 */
export type FileLineMap = readonly (number | null)[];

/** 全任务行映射：path → 单文件行映射（空 = 守恒/旧 sidecar，identity）。 */
export type CodeLineMaps = Map<string, FileLineMap>;

/** 放大目标：源图页标识 + 放大区域（原图像素，喂 CropZoomViewport）。 */
export interface MagnifierTarget {
  readonly page: string;
  readonly region: RegionBBox;
  /** 当前行高亮带的原图 bbox：横向铺满固定行宽参考（整行背景染色、不随行长缩放），
   *  纵向取当前行（无 bbox 时为就近回退行）真实 y；放大视图里整行染色标当前行（不描边遮正文）。 */
  readonly focus: LineBoxTuple;
}

/** 当前行无 bbox 时，向上下各扫描的最大行数，找最近有 bbox 的行回退。 */
const NEAREST_SCAN = 5;

/**
 * 文本中给定字符偏移所在的 0-based 行号（统计该偏移之前出现的换行数）。
 *
 * 供编辑态把 ``textarea.selectionStart`` 映射成行内偏移：再叠加
 * ``line_no_range[0]`` 即还原成 OCR ``line_no``（与只读 ``data-line`` 同源），
 * 喂 {@link computeMagnifierRegion}。``offset`` 越界（负 / 超长）按 ``[0, len]`` 钳制。
 */
export function lineIndexAtOffset(text: string, offset: number): number {
  const end = Math.max(0, Math.min(offset, text.length));
  let count = 0;
  for (let i = 0; i < end; i += 1) {
    if (text[i] === "\n") count += 1;
  }
  return count;
}

/** 从载荷建「path → (line_no → {page,bbox})」索引；同 line_no 保留首个（已去重防御）。 */
export function buildLineIndex(payload: CodeLayoutPayload): CodeLineIndex {
  const index: CodeLineIndex = new Map();
  for (const file of payload.files) {
    const fileIndex: FileLineIndex = new Map();
    for (const line of file.lines) {
      if (!fileIndex.has(line.line_no)) {
        fileIndex.set(line.line_no, { page: line.page, bbox: line.bbox });
      }
    }
    index.set(file.path, fileIndex);
  }
  return index;
}

/** 从载荷建「path → 行映射」（#5）。空映射(守恒/旧 sidecar)即 identity，调用方直接查表。 */
export function buildLineMaps(payload: CodeLayoutPayload): CodeLineMaps {
  const maps: CodeLineMaps = new Map();
  for (const file of payload.files) {
    maps.set(file.path, file.line_map);
  }
  return maps;
}

/**
 * 显示行序(0-based) → 原 OCR line_no，供放大镜查 bbox 前翻译精修后行号（#5）。
 *
 * - ``undefined``：无映射（守恒 / 旧 sidecar / 越界）→ 调用方走 identity（用 displayLineNumber 原值）；
 * - ``null``：该行被 rewrite/repair 改写或新增、无原图对应行 → 不放大（优于错放邻行）；
 * - ``number``：该行对应的原 OCR line_no（喂 {@link computeMagnifierRegion}）。
 */
export function mapDisplayLineToRaw(
  lineMap: FileLineMap | undefined,
  displayIndex: number,
): number | null | undefined {
  if (lineMap === undefined || lineMap.length === 0) return undefined;
  if (displayIndex < 0 || displayIndex >= lineMap.length) return undefined;
  return lineMap[displayIndex];
}

/** 多个 bbox 的外接并集 → RegionBBox（CropZoomViewport 的 region 形态）。 */
function unionBBox(boxes: readonly LineBoxTuple[]): RegionBBox {
  let x0 = Number.POSITIVE_INFINITY;
  let y0 = Number.POSITIVE_INFINITY;
  let x1 = Number.NEGATIVE_INFINITY;
  let y1 = Number.NEGATIVE_INFINITY;
  for (const [bx0, by0, bx1, by1] of boxes) {
    x0 = Math.min(x0, bx0);
    y0 = Math.min(y0, by0);
    x1 = Math.max(x1, bx1);
    y1 = Math.max(y1, by1);
  }
  return { x0, y0, x1, y1 };
}

/** 当前页所有行的 x 并集 → 固定「行宽参考」。横向恒用它而非逐行 bbox 宽度，
 *  使放大缩放不随行长变化（短行不被铺开拉大、字号稳定），只纵向跟随光标。 */
function pageXExtent(
  fileIndex: FileLineIndex,
  page: string,
): { readonly x0: number; readonly x1: number } {
  let x0 = Number.POSITIVE_INFINITY;
  let x1 = Number.NEGATIVE_INFINITY;
  for (const box of fileIndex.values()) {
    if (box.page !== page) continue;
    x0 = Math.min(x0, box.bbox[0]);
    x1 = Math.max(x1, box.bbox[2]);
  }
  return { x0, x1 };
}

/** 当前行无 bbox 时，向上下交替扫描最近有 bbox 的行（先下后上，稳定）。 */
function findNearest(
  fileIndex: FileLineIndex,
  lineNo: number,
): LineBox | undefined {
  for (let d = 1; d <= NEAREST_SCAN; d += 1) {
    const below = fileIndex.get(lineNo - d);
    if (below !== undefined) return below;
    const above = fileIndex.get(lineNo + d);
    if (above !== undefined) return above;
  }
  return undefined;
}

/**
 * 当前行 → 放大目标：以当前行（或就近回退行）所在页为锚点，并入 line±1 的同页 bbox。
 *
 * 无索引 / 空文件 / 就近窗口内仍无 bbox → undefined。
 */
export function computeMagnifierRegion(
  fileIndex: FileLineIndex | undefined,
  lineNo: number,
): MagnifierTarget | undefined {
  if (fileIndex === undefined || fileIndex.size === 0) return undefined;
  const anchor = fileIndex.get(lineNo) ?? findNearest(fileIndex, lineNo);
  if (anchor === undefined) return undefined;

  const { page } = anchor;
  // 纵向 band：当前行 ±1 的同页行 y 并集（跨页边界只并同页那侧）。
  const boxes: LineBoxTuple[] = [];
  for (const ln of [lineNo - 1, lineNo, lineNo + 1]) {
    const box = fileIndex.get(ln);
    if (box?.page === page) boxes.push(box.bbox);
  }
  // 当前行及相邻行都不在锚点页（当前行无 bbox、就近行在别页）→ 用就近行单框。
  if (boxes.length === 0) boxes.push(anchor.bbox);
  const band = unionBBox(boxes);
  // 横向恒取当前页固定行宽参考 → 缩放不随行长变化、短行不铺开；纵向用 band 跟随光标。
  const ref = pageXExtent(fileIndex, page);
  const region: RegionBBox = {
    x0: ref.x0, y0: band.y0, x1: ref.x1, y1: band.y1,
  };
  // 当前行高亮：横向铺满固定行宽参考（与 region 同宽 → 整行背景带，不随行长缩放、
  // 不描边遮正文），纵向取当前行（或就近回退行）真实 y。
  const focusLine = fileIndex.get(lineNo) ?? anchor;
  const focus: LineBoxTuple = [
    ref.x0, focusLine.bbox[1], ref.x1, focusLine.bbox[3],
  ];
  return { page, region, focus };
}
