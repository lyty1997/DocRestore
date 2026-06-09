/**
 * 四角校正编辑器：图片上叠加 4 个可拖拽角点（左上/右上/右下/左下），
 * 用户把它们对准倾斜 / 透视变形插图的四角，后端按此四边形透视矫正为正视图。
 *
 * 角点坐标用**原图像素**（与后端 quad 一致）；显示时按容器宽度等比缩放。
 * SVG 叠加层：四边形外区域压暗（evenodd 镂空）+ 描边四边形；4 个 HTML 手柄
 * 绝对定位在角点处，指针事件 + setPointerCapture 支持拖出容器。
 *
 * 角点角色固定（不按几何重排）：用户指定"这是左上角"即左上，故旋转的插图也能
 * 正确矫正（与后端 crop_quad_to_images 的"信任顺序"契约一致）。
 */

import { useRef } from "react";

import type { CropPoint, CropQuad } from "../api/schemas";

/** 四角角色（即透视变换的源点顺序）。 */
type Corner = "tl" | "tr" | "br" | "bl";

const CORNERS: readonly Corner[] = ["tl", "tr", "br", "bl"];

interface QuadCropEditorProps {
  readonly imageUrl: string;
  readonly naturalWidth: number;
  readonly naturalHeight: number;
  readonly quad: CropQuad;
  readonly onChange: (quad: CropQuad) => void;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

export function QuadCropEditor({
  imageUrl,
  naturalWidth,
  naturalHeight,
  quad,
  onChange,
}: QuadCropEditorProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<
    | { corner: Corner; startX: number; startY: number; startPoint: CropPoint }
    | undefined
  >(undefined);

  const onHandleDown =
    (corner: Corner) =>
    (e: React.PointerEvent<HTMLElement>): void => {
      e.preventDefault();
      e.stopPropagation();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = {
        corner,
        startX: e.clientX,
        startY: e.clientY,
        startPoint: quad[corner],
      };
    };

  const onPointerMove = (e: React.PointerEvent<HTMLElement>): void => {
    const drag = dragRef.current;
    const container = containerRef.current;
    if (drag === undefined || container === null) return;
    const scale = naturalWidth / container.clientWidth; // 原图像素 / 显示像素
    const dx = (e.clientX - drag.startX) * scale;
    const dy = (e.clientY - drag.startY) * scale;
    const next: CropPoint = {
      x: Math.round(clamp(drag.startPoint.x + dx, 0, naturalWidth - 1)),
      y: Math.round(clamp(drag.startPoint.y + dy, 0, naturalHeight - 1)),
    };
    onChange({ ...quad, [drag.corner]: next });
  };

  const onPointerUp = (e: React.PointerEvent<HTMLElement>): void => {
    if (dragRef.current !== undefined) {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      dragRef.current = undefined;
    }
  };

  const pts = [quad.tl, quad.tr, quad.br, quad.bl];
  const polyPoints = pts
    .map((p) => `${p.x.toString()},${p.y.toString()}`)
    .join(" ");
  // 外矩形（顺时针）+ 四边形（同向）配 evenodd → 填充四边形以外区域（压暗）
  const maskPath =
    `M0 0 H${naturalWidth.toString()} V${naturalHeight.toString()} H0 Z `
    + `M${quad.tl.x.toString()} ${quad.tl.y.toString()} `
    + `L${quad.tr.x.toString()} ${quad.tr.y.toString()} `
    + `L${quad.br.x.toString()} ${quad.br.y.toString()} `
    + `L${quad.bl.x.toString()} ${quad.bl.y.toString()} Z`;

  return (
    <div
      ref={containerRef}
      className="quad-editor"
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <img className="quad-editor-img" src={imageUrl} alt="" draggable={false} />
      <svg
        className="quad-editor-svg"
        viewBox={`0 0 ${naturalWidth.toString()} ${naturalHeight.toString()}`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path className="quad-editor-mask" d={maskPath} fillRule="evenodd" />
        <polygon
          className="quad-editor-poly"
          points={polyPoints}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {CORNERS.map((c) => {
        const p = quad[c];
        return (
          <span
            key={c}
            className="quad-editor-handle"
            onPointerDown={onHandleDown(c)}
            style={{
              left: `${((p.x / naturalWidth) * 100).toString()}%`,
              top: `${((p.y / naturalHeight) * 100).toString()}%`,
            }}
          />
        );
      })}
    </div>
  );
}
