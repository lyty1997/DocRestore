/**
 * Markdown 图片 URL 重写
 *
 * 将 markdown 中 `images/...` 引用重写为后端 assets 接口路径。
 */

import { getAssetUrl } from "../../api/client";

/**
 * 重写 markdown 中的图片引用路径
 *
 * 将 `![alt](images/xxx.jpg)` 重写为
 * `![alt](/api/v1/tasks/{taskId}/assets/images/xxx.jpg)`
 */
export function rewriteImageUrls(markdown: string, taskId: string): string {
  // 匹配 ![...](images/...) 格式，不匹配已经是绝对路径或 http 的
  return markdown.replaceAll(
    /!\[([^\]]*)\]\((images\/[^)]+)\)/g,
    (_match, alt: string, src: string) => {
      const newSrc = getAssetUrl(taskId, src);
      return `![${alt}](${newSrc})`;
    },
  );
}
