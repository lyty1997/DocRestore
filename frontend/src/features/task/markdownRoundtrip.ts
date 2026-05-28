/**
 * Markdown ⇄ HTML 双向转换（供 Tiptap WYSIWYG 编辑器用）。
 *
 * - md → html：marked + GFM；HTML 注释 ``<!-- page: X -->`` 转成自定义
 *   ``<div data-page-anchor data-page="X">`` 块保留位置信息
 * - html → md：turndown + GFM；自定义 page-anchor 块还原为 HTML 注释
 *
 * 设计要点：
 *   - 不重写图片 URL（编辑器内部展示靠 ``rewriteImageUrls`` 在加载时跑；
 *     保存时 turndown 输出原始路径，与后端 markdown.ts 约定一致）
 *   - HTML 注释里 ``--`` 序列非法，所以 page 名称中的连字符用单个 ``-``
 */
import { marked } from "marked";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

const PAGE_ANCHOR_RE = /<!--\s*page:\s*([^>]+?)\s*-->/g;

/**
 * 把 markdown 转为可塞进 Tiptap 的 HTML。
 *
 * - GFM 语法（表格 / 删除线 / 任务列表）开启
 * - 同步模式（``async: false``）便于直接返回 string
 * - page anchor 注释转成自定义 div，让 Tiptap 当作 atom block 保留
 */
export function markdownToHtml(markdown: string): string {
  const withAnchorBlocks = markdown.replaceAll(
    PAGE_ANCHOR_RE,
    (_match, name: string) => {
      const safe = name.trim().replaceAll('"', "&quot;");
      // div 必须含可见内容，否则 turndown 的 blank-detection 会把整个块
      // 当成空白丢掉，page-anchor rule 永远进不来。
      return `<div data-page-anchor data-page="${safe}">📄 ${safe}</div>\n`;
    },
  );
  marked.setOptions({ gfm: true, breaks: false, async: false });
  return marked.parse(withAnchorBlocks) as string;
}


/** turndown 单例（带 GFM + 自定义 page-anchor 还原规则）。 */
let _turndown: TurndownService | undefined;

function getTurndown(): TurndownService {
  if (_turndown) return _turndown;
  const td = new TurndownService({
    headingStyle: "atx",          // # H1 风格（不是 setext）
    bulletListMarker: "-",
    codeBlockStyle: "fenced",
    fence: "```",
    emDelimiter: "*",
    strongDelimiter: "**",
    linkStyle: "inlined",
  });
  td.use(gfm);

  // 自定义规则：page-anchor div → HTML 注释
  td.addRule("pageAnchor", {
    filter: (node: HTMLElement): boolean =>
      node.nodeName === "DIV"
      && node.dataset.pageAnchor !== undefined,
    replacement: (_content: string, node: Node): string => {
      const el = node as HTMLElement;
      const page = el.dataset.page ?? "";
      return `\n<!-- page: ${page} -->\n`;
    },
  });

  // 防止 turndown 把行内 HTML 注释（来自 OCR 的 source_pages 注释等）丢掉
  td.addRule("htmlComment", {
    filter: (node: Node): boolean => node.nodeType === Node.COMMENT_NODE,
    replacement: (_content: string, node: Node): string => {
      const text = (node as Comment).data;
      return `\n<!--${text}-->\n`;
    },
  });

  _turndown = td;
  return td;
}

/**
 * 把 Tiptap 输出的 HTML 转回 markdown。
 *
 * - GFM 表格 / 删除线
 * - 自定义 page-anchor div 还原为 ``<!-- page: X -->`` 注释
 * - 输出末尾保证有单个换行
 */
export function htmlToMarkdown(html: string): string {
  const md = getTurndown().turndown(html);
  return md.endsWith("\n") ? md : `${md}\n`;
}
