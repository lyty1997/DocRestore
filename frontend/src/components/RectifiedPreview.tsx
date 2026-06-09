/**
 * 矫正结果实时预览：把源图按四边形（tl/tr/br/bl，源图像素坐标）透视矫正成
 * 正视矩形，随框 / 角点拖动实时更新。用 CSS ``matrix3d`` 由 GPU 变换，无需后端
 * 往返；方向与后端 ``warp_quad`` 一致，所见即所得。
 *
 * 矩形裁剪与四角校正共用：矩形模式传入由框生成的轴对齐四边形（退化为纯裁剪）。
 */

import type { CropQuad } from "../api/schemas";
import { matrix3dFromQuad, type Point } from "../features/task/perspective";

/** 预览框最大尺寸（CSS 像素）；超出按比例缩放、过小则放大铺满。 */
const MAX_W = 320;
const MAX_H = 360;

interface RectifiedPreviewProps {
  readonly imageUrl: string;
  readonly naturalWidth: number;
  readonly naturalHeight: number;
  /** 源图上的四边形（源图像素坐标）。 */
  readonly quad: CropQuad;
}

function dist(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

export function RectifiedPreview({
  imageUrl,
  naturalWidth,
  naturalHeight,
  quad,
}: RectifiedPreviewProps): React.JSX.Element {
  // 矫正后正矩形尺寸 = 四边形上下边 / 左右边的最大长度（与后端 warp_quad 同口径）
  const outW = Math.max(dist(quad.tr, quad.tl), dist(quad.br, quad.bl));
  const outH = Math.max(dist(quad.bl, quad.tl), dist(quad.br, quad.tr));
  if (outW < 1 || outH < 1) {
    return <div className="figure-crop-preview-box figure-crop-preview-empty" />;
  }
  // 等比缩放到预览框上限内
  const scale = Math.min(MAX_W / outW, MAX_H / outH);
  const pw = Math.round(outW * scale);
  const ph = Math.round(outH * scale);
  const transform = matrix3dFromQuad(
    quad.tl, quad.tr, quad.br, quad.bl, pw, ph,
  );

  return (
    <div
      className="figure-crop-preview-box"
      style={{ width: pw, height: ph }}
    >
      <img
        src={imageUrl}
        alt=""
        draggable={false}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: naturalWidth,
          height: naturalHeight,
          transformOrigin: "0 0",
          transform,
        }}
      />
    </div>
  );
}
