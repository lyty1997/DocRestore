/**
 * 带 page 锚点的源图列表。
 *
 * 文档模式和代码模式都依赖同一约定：
 * - 每张原图元素写入 `data-page="<pageKey>"`
 * - 外层滚动容器通过 ref 暴露给同步滚动 hook
 * - 点击原图打开通用 lightbox
 */

import { forwardRef, useState } from "react";

import { getProcessedImageUrl, getSourceImageUrl } from "../api/client";
import type { SourceImageHighlight } from "../features/task/blockHighlight";
import type { SourceImageListItem } from "../features/task/sourceImagePreview";
import { BlockHighlightOverlay } from "./BlockHighlightOverlay";
import { ImageLightbox } from "./ImageLightbox";

interface SourceImageListProps {
  readonly taskId: string;
  readonly images: readonly SourceImageListItem[];
  readonly listClassName: string;
  readonly imageClassName: string;
  readonly empty?: React.ReactNode;
  /** 光标块 bbox 高亮（仅命中页那张图叠矩形）；缺省不高亮。 */
  readonly highlight?: SourceImageHighlight | undefined;
  /** 处理图高亮：true 时改显处理图（PPT 矫正 ``_after`` / content_crop 裁剪 ``_crop``，
   *  bbox 同坐标系才对齐，§13/§15）；data-page/pageKey 仍用原图名保持三键对齐；该页无
   *  处理图（404）时 onError 回退原图。 */
  readonly processed?: boolean;
  /** 多文档相对子目录，构造处理图 URL 用（单文档留空）。 */
  readonly docDir?: string | undefined;
}

export const SourceImageList = forwardRef<
  HTMLDivElement,
  SourceImageListProps
>(function SourceImageList(
  {
    taskId, images, listClassName, imageClassName, empty, highlight,
    processed = false, docDir,
  },
  scrollRef,
): React.JSX.Element {
  const [lightboxSrc, setLightboxSrc] = useState<string | undefined>();

  return (
    <>
      <div ref={scrollRef} className={listClassName}>
        {images.length === 0 && empty}
        {images.map((image) => {
          const src = getSourceImageUrl(taskId, image.name);
          // 处理图高亮：改显处理图(pageKey 仍原图名保三键对齐)；其余/缺失显原图。
          const displaySrc = processed
            ? getProcessedImageUrl(taskId, image.pageKey, docDir)
            : src;
          const hit =
            highlight?.pageKey === image.pageKey ? highlight : undefined;
          return (
            <div
              key={image.name}
              className="source-image-cell"
              data-page={image.pageKey}
            >
              <img
                src={displaySrc}
                alt={image.name}
                title={image.name}
                className={imageClassName}
                onClick={() => { setLightboxSrc(displaySrc); }}
                onError={
                  displaySrc === src
                    ? undefined
                    : (event) => {
                        // 该页无处理图（bbox 本就在原图坐标）→ 一次性回退原图。
                        const img = event.currentTarget;
                        if (img.dataset.fellBack !== "1") {
                          img.dataset.fellBack = "1";
                          img.src = src;
                        }
                      }
                }
              />
              {hit !== undefined && (
                <BlockHighlightOverlay
                  bbox={hit.bbox}
                  imageSize={hit.imageSize}
                />
              )}
            </div>
          );
        })}
      </div>
      <ImageLightbox
        src={lightboxSrc}
        onClose={() => { setLightboxSrc(undefined); }}
      />
    </>
  );
});
