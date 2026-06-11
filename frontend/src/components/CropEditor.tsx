/**
 * 正文裁剪框编辑器：图片上叠加一个可拖拽（移动）/ 可缩放（8 个手柄）的矩形框。
 *
 * 框坐标用**原图像素**（与后端 detect / crop_boxes 一致）；显示时按容器宽度等比缩放。
 * 框外区域用 box-shadow 压暗，突出正文区。指针事件 + setPointerCapture 支持拖出容器。
 */

import { useRef } from "react";

import type { CropBox } from "../api/schemas";

type DragMode = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

const HANDLES: readonly DragMode[] = [
  "nw", "n", "ne", "e", "se", "s", "sw", "w",
];
const MIN_SIDE = 16; // 框最小边长（原图像素）

interface CropEditorProps {
  readonly imageUrl: string;
  readonly naturalWidth: number;
  readonly naturalHeight: number;
  readonly box: CropBox;
  readonly onChange: (box: CropBox) => void;
  /** 一次拖拽（移动/缩放框）结束时回调，供外层做松手后的视图联动。 */
  readonly onDragEnd?: () => void;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function axisPos(neg: boolean, pos: boolean): string {
  if (neg) return "0%";
  if (pos) return "100%";
  return "50%";
}

function handleCursor(h: DragMode): string {
  if (h === "n" || h === "s") return "ns-resize";
  if (h === "e" || h === "w") return "ew-resize";
  if (h === "nw" || h === "se") return "nwse-resize";
  return "nesw-resize";
}

function handleStyle(h: DragMode): { left: string; top: string; cursor: string } {
  return {
    left: axisPos(h.includes("w"), h.includes("e")),
    top: axisPos(h.includes("n"), h.includes("s")),
    cursor: handleCursor(h),
  };
}

export function CropEditor({
  imageUrl,
  naturalWidth,
  naturalHeight,
  box,
  onChange,
  onDragEnd,
}: CropEditorProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<
    | { mode: DragMode; startX: number; startY: number; startBox: CropBox }
    | undefined
  >(undefined);

  const onHandleDown =
    (mode: DragMode) =>
    (e: React.PointerEvent<HTMLElement>): void => {
      e.preventDefault();
      e.stopPropagation();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = {
        mode,
        startX: e.clientX,
        startY: e.clientY,
        startBox: box,
      };
    };

  const onPointerMove = (e: React.PointerEvent<HTMLElement>): void => {
    const drag = dragRef.current;
    const container = containerRef.current;
    if (drag === undefined || container === null) return;
    // 原图像素 / 显示像素；用 getBoundingClientRect 使外层 transform scale 也计入
    const displayWidth = container.getBoundingClientRect().width;
    if (displayWidth <= 0) return;
    const scale = naturalWidth / displayWidth;
    const dx = (e.clientX - drag.startX) * scale;
    const dy = (e.clientY - drag.startY) * scale;
    const s = drag.startBox;
    let { x0, y0, x1, y1 } = s;
    if (drag.mode === "move") {
      const bw = s.x1 - s.x0;
      const bh = s.y1 - s.y0;
      x0 = clamp(s.x0 + dx, 0, naturalWidth - bw);
      y0 = clamp(s.y0 + dy, 0, naturalHeight - bh);
      x1 = x0 + bw;
      y1 = y0 + bh;
    } else {
      if (drag.mode.includes("w")) x0 = clamp(s.x0 + dx, 0, s.x1 - MIN_SIDE);
      if (drag.mode.includes("e")) x1 = clamp(s.x1 + dx, s.x0 + MIN_SIDE, naturalWidth);
      if (drag.mode.includes("n")) y0 = clamp(s.y0 + dy, 0, s.y1 - MIN_SIDE);
      if (drag.mode.includes("s")) y1 = clamp(s.y1 + dy, s.y0 + MIN_SIDE, naturalHeight);
    }
    onChange({
      x0: Math.round(x0),
      y0: Math.round(y0),
      x1: Math.round(x1),
      y1: Math.round(y1),
    });
  };

  const onPointerUp = (e: React.PointerEvent<HTMLElement>): void => {
    if (dragRef.current !== undefined) {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      dragRef.current = undefined;
      onDragEnd?.();
    }
  };

  const left = (box.x0 / naturalWidth) * 100;
  const top = (box.y0 / naturalHeight) * 100;
  const width = ((box.x1 - box.x0) / naturalWidth) * 100;
  const height = ((box.y1 - box.y0) / naturalHeight) * 100;

  // 框外压暗：上 / 下 / 左 / 右四块精确遮罩（不能用 9999px box-shadow——溢出
  // 图区且无法裁剪：裁剪会切掉贴边手柄，多编辑器同屏时压暗还会层层叠加致全黑）
  const shades: React.CSSProperties[] = [
    { left: 0, top: 0, width: "100%", height: `${top.toString()}%` },
    {
      left: 0,
      top: `${(top + height).toString()}%`,
      width: "100%",
      height: `${(100 - top - height).toString()}%`,
    },
    {
      left: 0,
      top: `${top.toString()}%`,
      width: `${left.toString()}%`,
      height: `${height.toString()}%`,
    },
    {
      left: `${(left + width).toString()}%`,
      top: `${top.toString()}%`,
      width: `${(100 - left - width).toString()}%`,
      height: `${height.toString()}%`,
    },
  ];

  return (
    <div
      ref={containerRef}
      className="crop-editor"
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <img className="crop-editor-img" src={imageUrl} alt="" draggable={false} />
      {shades.map((style, i) => (
        // 固定四块（上下左右），顺序稳定，索引即身份
        <div key={i} className="crop-editor-shade" style={style} />
      ))}
      <div
        className="crop-editor-box"
        onPointerDown={onHandleDown("move")}
        style={{
          left: `${left.toString()}%`,
          top: `${top.toString()}%`,
          width: `${width.toString()}%`,
          height: `${height.toString()}%`,
        }}
      >
        {HANDLES.map((h) => {
          const hs = handleStyle(h);
          return (
            <span
              key={h}
              className="crop-editor-handle"
              onPointerDown={onHandleDown(h)}
              style={{ left: hs.left, top: hs.top, cursor: hs.cursor }}
            />
          );
        })}
      </div>
    </div>
  );
}
