/**
 * 上传文件类别判定（Epic A A2：PDF 输入支持）。
 *
 * 同一批输入要么全是图片、要么全是 PDF，二者互斥——前端在选择时先行预校验，
 * 给出即时提示；后端 upload_files（闸一）与 create_task（闸二）做最终强制。
 *
 * 纯逻辑模块，便于单测，不依赖 React / DOM 渲染。
 */

/** 支持的图片扩展名（小写，含点） */
export const IMAGE_EXTENSIONS: ReadonlySet<string> = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".bmp",
  ".tiff",
  ".tif",
]);

/** PDF 扩展名（小写，含点） */
export const PDF_EXTENSION = ".pdf";

/** 全部允许的扩展名（图片 + PDF），供 input 白名单过滤用 */
export const ALLOWED_EXTENSIONS: ReadonlySet<string> = new Set([
  ...IMAGE_EXTENSIONS,
  PDF_EXTENSION,
]);

/** `<input accept>` 属性：图片 MIME + PDF */
export const ACCEPT_ATTR =
  "image/jpeg,image/png,image/bmp,image/tiff,application/pdf";

/** 单个文件的类别 */
export type FileKind = "image" | "pdf";

/** 一批选择的整体类别 */
export type SelectionKind = "image" | "pdf" | "mixed" | "empty";

/** 取文件名扩展名（小写，含点）；无扩展名返回 undefined */
function extensionOf(name: string): string | undefined {
  const dot = name.lastIndexOf(".");
  if (dot === -1) return undefined;
  return name.slice(dot).toLowerCase();
}

/** 判定单个文件类别；不支持的扩展名返回 undefined */
export function fileKind(name: string): FileKind | undefined {
  const ext = extensionOf(name);
  if (ext === undefined) return undefined;
  if (ext === PDF_EXTENSION) return "pdf";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  return undefined;
}

/** 文件名是否 PDF（按扩展名，大小写不敏感） */
export function isPdfFilename(name: string): boolean {
  return fileKind(name) === "pdf";
}

/** 文件名是否受支持（图片或 PDF） */
export function isAcceptedFilename(name: string): boolean {
  return fileKind(name) !== undefined;
}

/**
 * 对一组文件名整体分类：
 * - `image` / `pdf`：全部为同一类（忽略不支持的文件）
 * - `mixed`：同时含图片与 PDF（互斥违例）
 * - `empty`：没有任何受支持文件
 */
export function classifySelection(names: readonly string[]): SelectionKind {
  let hasImage = false;
  let hasPdf = false;
  for (const name of names) {
    const kind = fileKind(name);
    if (kind === "image") hasImage = true;
    else if (kind === "pdf") hasPdf = true;
  }
  if (hasImage && hasPdf) return "mixed";
  if (hasImage) return "image";
  if (hasPdf) return "pdf";
  return "empty";
}
