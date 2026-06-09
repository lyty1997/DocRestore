/**
 * 4 点投影变换（单应性）→ CSS ``matrix3d``：把源图任意四边形实时矫正成正视矩形。
 *
 * 用于编辑器「实时渲染矫正截图」预览：给源图 4 角（左上/右上/右下/左下）和目标
 * 矩形尺寸，算出把该四边形映射到 ``[0,0]–[w,h]`` 的投影矩阵，套在 ``<img>`` 上由
 * GPU 实时变换。方向与后端 ``slide_rectify.warp_quad`` 一致（源四边形 → 正矩形）。
 *
 * 算法为经典 4 点投影标定（adjugate / 基底映射），纯函数、无依赖。
 */

/** 行主序 3×3 矩阵。 */
type Mat3 = readonly [
  number, number, number,
  number, number, number,
  number, number, number,
];
/** 3 维（齐次）向量。 */
type Vec3 = readonly [number, number, number];

/** 平面点（这里用源图像素坐标）。 */
export interface Point {
  readonly x: number;
  readonly y: number;
}

/** 3×3 伴随矩阵（用于求逆方向的映射）。 */
function adjugate(m: Mat3): Mat3 {
  const [a, b, c, d, e, f, g, h, i] = m;
  return [
    e * i - f * h, c * h - b * i, b * f - c * e,
    f * g - d * i, a * i - c * g, c * d - a * f,
    d * h - e * g, b * g - a * h, a * e - b * d,
  ];
}

/** 3×3 矩阵乘法。 */
function multmm(x: Mat3, y: Mat3): Mat3 {
  const [a, b, c, d, e, f, g, h, i] = x;
  const [j, k, l, m, n, o, p, q, r] = y;
  return [
    a * j + b * m + c * p, a * k + b * n + c * q, a * l + b * o + c * r,
    d * j + e * m + f * p, d * k + e * n + f * q, d * l + e * o + f * r,
    g * j + h * m + i * p, g * k + h * n + i * q, g * l + h * o + i * r,
  ];
}

/** 3×3 矩阵 × 3 向量。 */
function multmv(m: Mat3, v: Vec3): Vec3 {
  const [a, b, c, d, e, f, g, h, i] = m;
  const [x, y, z] = v;
  return [a * x + b * y + c * z, d * x + e * y + f * z, g * x + h * y + i * z];
}

/** 把单位基底映射到 4 个点（投影标定的核心步骤）。 */
function basisToPoints(
  x1: number, y1: number, x2: number, y2: number,
  x3: number, y3: number, x4: number, y4: number,
): Mat3 {
  const m: Mat3 = [x1, x2, x3, y1, y2, y3, 1, 1, 1];
  const [v0, v1, v2] = multmv(adjugate(m), [x4, y4, 1]);
  return multmm(m, [v0, 0, 0, 0, v1, 0, 0, 0, v2]);
}

/** 求把 4 个源点映射到 4 个目标点的投影矩阵（源点 i → 目标点 i）。 */
function general2DProjection(
  src: readonly [Point, Point, Point, Point],
  dst: readonly [Point, Point, Point, Point],
): Mat3 {
  const s = basisToPoints(
    src[0].x, src[0].y, src[1].x, src[1].y,
    src[2].x, src[2].y, src[3].x, src[3].y,
  );
  const d = basisToPoints(
    dst[0].x, dst[0].y, dst[1].x, dst[1].y,
    dst[2].x, dst[2].y, dst[3].x, dst[3].y,
  );
  return multmm(d, adjugate(s));
}

/**
 * 求把源图四边形（tl/tr/br/bl，源图像素坐标）映射到 ``w×h`` 正矩形的投影矩阵。
 *
 * 角点顺序与后端 ``warp_quad`` 一致：tl→(0,0)、tr→(w,0)、br→(w,h)、bl→(0,h)。
 */
export function quadToRectProjection(
  tl: Point, tr: Point, br: Point, bl: Point, w: number, h: number,
): Mat3 {
  return general2DProjection(
    [tl, tr, br, bl],
    [{ x: 0, y: 0 }, { x: w, y: 0 }, { x: w, y: h }, { x: 0, y: h }],
  );
}

/** 用投影矩阵变换一个点（齐次除法后的笛卡尔坐标）。供校验 / 测试。 */
export function projectPoint(m: Mat3, p: Point): Point {
  const [x, y, w] = multmv(m, [p.x, p.y, 1]);
  return { x: x / w, y: y / w };
}

/**
 * 生成 CSS ``matrix3d(...)`` 字符串：套在按源图自然尺寸渲染的 ``<img>`` 上
 * （``transform-origin: 0 0``），即把源图四边形实时矫正铺满 ``w×h`` 预览框。
 */
export function matrix3dFromQuad(
  tl: Point, tr: Point, br: Point, bl: Point, w: number, h: number,
): string {
  const t = quadToRectProjection(tl, tr, br, bl, w, h);
  const [t0, t1, t2, t3, t4, t5, t6, t7, t8] = t;
  // 归一化（除以 t8）后按 CSS matrix3d 的列主序排布投影项
  const m3d = [
    t0 / t8, t3 / t8, 0, t6 / t8,
    t1 / t8, t4 / t8, 0, t7 / t8,
    0, 0, 1, 0,
    t2 / t8, t5 / t8, 0, t8 / t8,
  ];
  return `matrix3d(${m3d.map((n) => n.toString()).join(",")})`;
}
