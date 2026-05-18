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
import type { SourceImageListItem } from "../features/task/sourceImagePreview";
import { ImageLightbox } from "./ImageLightbox";

interface SourceImageListProps {
  readonly taskId: string;
  readonly images: readonly SourceImageListItem[];
  readonly listClassName: string;
  readonly imageClassName: string;
  readonly empty?: React.ReactNode;
}

export const SourceImageList = forwardRef<
  HTMLDivElement,
  SourceImageListProps
>(function SourceImageList(
  { taskId, images, listClassName, imageClassName, empty },
  scrollRef,
): React.JSX.Element {
  const [lightboxSrc, setLightboxSrc] = useState<string | undefined>();

  return (
    <>
      <div ref={scrollRef} className={listClassName}>
        {images.length === 0 && empty}
        {images.map((image) => {
          const src = getSourceImageUrl(taskId, image.name);
          return (
            <img
              key={image.name}
              src={src}
              alt={image.name}
              title={image.name}
              data-page={image.pageKey}
              className={imageClassName}
              onClick={() => { setLightboxSrc(src); }}
            />
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
