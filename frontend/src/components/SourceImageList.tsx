/**
 * 带 page 锚点的源图列表。
 *
 * 文档模式和代码模式都依赖同一约定：
 * - 每张原图元素写入 `data-page="<pageKey>"`
 * - 外层滚动容器通过 ref 暴露给同步滚动 hook
 * - 点击原图打开通用 lightbox
 */

import { forwardRef, useState } from "react";

import { getSourceImageUrl } from "../api/client";
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
}

export const SourceImageList = forwardRef<
  HTMLDivElement,
  SourceImageListProps
>(function SourceImageList(
  { taskId, images, listClassName, imageClassName, empty, highlight },
  scrollRef,
): React.JSX.Element {
  const [lightboxSrc, setLightboxSrc] = useState<string | undefined>();

  return (
    <>
      <div ref={scrollRef} className={listClassName}>
        {images.length === 0 && empty}
        {images.map((image) => {
          const src = getSourceImageUrl(taskId, image.name);
          const hit =
            highlight?.pageKey === image.pageKey ? highlight : undefined;
          return (
            <div
              key={image.name}
              className="source-image-cell"
              data-page={image.pageKey}
            >
              <img
                src={src}
                alt={image.name}
                title={image.name}
                className={imageClassName}
                onClick={() => { setLightboxSrc(src); }}
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
