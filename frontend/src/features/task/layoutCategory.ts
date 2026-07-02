/**
 * 版面块分类 → 颜色（版面全览 E8/#92）。
 *
 * 参考 MinerU ``draw_bbox.py`` 的「每类一色」着色思路（借思路不抄代码，设计 §14.2）：
 * 把 PaddleOCR-VL 原生细粒度 ``label`` 按语义分组映射到一组区分度高的颜色，供
 * ``LayoutOverlay`` 画彩色分类框（边框实色 + 低透明填充）。未知 label 落 fallback 灰，
 * 保证新类型不崩、只是不着专属色。纯函数、无 DOM 依赖，便于单测与快照。
 */

/** RGB 三元（0..255）。 */
type Rgb = readonly [number, number, number];

/** 分类框颜色：边框实色 + 半透明填充（同色 alpha）。 */
export interface CategoryColor {
  /** 边框 / 角标底色（实色）。 */
  readonly border: string;
  /** 填充色（同色，低 alpha，不遮正文）。 */
  readonly fill: string;
}

/** 填充透明度：够淡以透出原图正文，又能区分类别。 */
const FILL_ALPHA = 0.15;
/** 未知 label 的兜底色（中性灰）。 */
const FALLBACK_RGB: Rgb = [120, 120, 120];

/**
 * label → RGB。同语义组共用一色（标题蓝 / 正文绿 / 表格橙 / 图紫 / 公式青 /
 * 页眉页脚灰 / 参考粉 / 印章金）；PaddleOCR-VL 细粒度类型未来新增 → 落 fallback。
 */
const CATEGORY_RGB: Readonly<Record<string, Rgb>> = {
  // 标题类（蓝）
  paragraph_title: [37, 99, 235],
  title: [37, 99, 235],
  doc_title: [37, 99, 235],
  // 正文类（绿）
  text: [22, 163, 74],
  abstract: [22, 163, 74],
  content: [22, 163, 74],
  // 表格类（橙）
  table: [234, 88, 12],
  table_caption: [234, 88, 12],
  table_title: [234, 88, 12],
  // 图 / 图表类（紫）
  image: [147, 51, 234],
  chart: [147, 51, 234],
  figure: [147, 51, 234],
  figure_caption: [147, 51, 234],
  figure_title: [147, 51, 234],
  // 公式类（青）
  formula: [13, 148, 136],
  formula_caption: [13, 148, 136],
  // 页眉 / 页脚 / 页码（灰）
  header: [100, 116, 139],
  footer: [100, 116, 139],
  page_number: [100, 116, 139],
  // 参考文献（粉）
  reference: [219, 39, 119],
  // 印章（金）
  seal: [202, 138, 4],
};

/**
 * 版面块 ``label`` → 分类框颜色（边框实色 + 同色低透明填充）。
 *
 * 未知 / 空 label 落 fallback 灰。用经典 ``rgb()`` / ``rgba()`` 逗号语法（浏览器与
 * jsdom cssstyle 全支持，避现代 ``rgb(r g b / a)`` 空格语法在测试环境被丢弃）。
 */
export function categoryColor(label: string): CategoryColor {
  const [r, g, b] = CATEGORY_RGB[label] ?? FALLBACK_RGB;
  const rgb = `${r.toString()}, ${g.toString()}, ${b.toString()}`;
  return {
    border: `rgb(${rgb})`,
    fill: `rgba(${rgb}, ${FILL_ALPHA.toString()})`,
  };
}
