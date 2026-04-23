/**
 * Markdown 预处理：图片 URL 重写 + 非法 HTML 标签转义
 */

import { getAssetUrl } from "../../api/client";

/**
 * OCR 输出中允许的 HTML 标签白名单。
 * 不在此列表中的 <xxx> 会被转义为 &lt;xxx&gt;，
 * 防止 rehype-raw 将 OCR 文本（如 <Select> <Exit>）解析为 HTML 元素导致渲染截断。
 */
const ALLOWED_TAG_RE =
  /^\/?\s*(?:div|img|table|thead|tbody|tr|td|th|br|hr|span|p|b|i|u|strong|em|sub|sup|ul|ol|li|a|code|pre|h[1-6]|blockquote)\b/i;

/**
 * 把后端 `PageDeduplicator.merge_all_pages` 插入的 `<!-- page: xxx.jpg -->`
 * 注释转成可定位的隐藏锚点，供左右同步滚动 hook 定位。
 *
 * - 注释本来在 rehype-raw 里会被丢弃，变成不可见节点
 * - 转成 `<span class="page-anchor" data-page="...">` 后既能被 querySelectorAll
 *   找到，也不会破坏视觉（CSS 里设零高度 / display:block）
 */
export function injectPageAnchors(text: string): string {
  // 注意：HTML 注释内不能含 `--`，但文件名可以含单个 `-`（如 DSC04696-2.jpg）。
  // 用 `.+?` 非贪婪匹配到 `-->` 之前即可。
  return text.replaceAll(
    /<!--\s*page:\s*(.+?)\s*-->/g,
    (_match, name: string) => {
      const safe = name.trim().replaceAll('"', "&quot;");
      return `<span class="page-anchor" data-page="${safe}"></span>`;
    },
  );
}

/** 转义非白名单 HTML 标签，保留合法的 OCR 产出标签 */
export function escapeNonHtmlTags(text: string): string {
  return text.replaceAll(/<([^>]*)>/g, (match, inner: string) => {
    if (ALLOWED_TAG_RE.test(inner)) return match;
    return `&lt;${inner}&gt;`;
  });
}

/**
 * 重写 markdown / HTML 中的图片引用路径
 *
 * - `images/xxx.jpg` → 重写为后端 assets 接口路径
 * - `XXX_OCR/images/...` → OCR 中间产物，移除（不存在实际文件）
 */
/**
 * 预处理 markdown：重写图片 URL + 转义非法 HTML 标签
 *
 * 调用顺序：先处理图片引用，再转义非白名单标签，
 * 确保合法的 <img> 标签不会被误转义。
 */
export function preprocessMarkdown(
  markdown: string,
  taskId: string,
  docDir?: string,
): string {
  const withAnchors = injectPageAnchors(markdown);
  const rewritten = rewriteImageUrls(withAnchors, taskId, docDir);
  return escapeNonHtmlTags(rewritten);
}

export function rewriteImageUrls(
  markdown: string,
  taskId: string,
  docDir?: string,
): string {
  // 1. 移除 OCR 中间产物的图片引用（XXX_OCR/images/...），这些文件不存在
  //    markdown 格式
  let result = markdown.replaceAll(
    /!\[[^\]]*\]\([^)/]+_OCR\/images\/[^)]+\)/g,
    "",
  );
  //    HTML <img> 标签（含外层可能的空 <td> 等）
  result = result.replaceAll(
    /<img\s[^>]*src=["'][^"'/]+_OCR\/images\/[^"']+["'][^>]*\/?>/g,
    "",
  );

  // 2. 重写有效的 images/ 路径（markdown 格式）
  //    多文档时在 images/ 前加 docDir 前缀，匹配 assets 白名单
  const assetPrefix = docDir ? `${docDir}/` : "";
  result = result.replaceAll(
    /!\[([^\]]*)\]\((images\/[^)]+)\)/g,
    (_match, alt: string, src: string) => {
      const newSrc = getAssetUrl(taskId, `${assetPrefix}${src}`);
      return `![${alt}](${newSrc})`;
    },
  );

  // 3. 重写有效的 images/ 路径（HTML <img> 标签）
  result = result.replaceAll(
    /(<img\s[^>]*?)src=(["'])(images\/[^"']+)\2/g,
    (_match, prefix: string, quote: string, src: string) => {
      const newSrc = getAssetUrl(taskId, `${assetPrefix}${src}`);
      return `${prefix}src=${quote}${newSrc}${quote}`;
    },
  );

  return result;
}
