/**
 * 源图片面板：展示任务的原始输入图片，支持点击放大查看。
 *
 * 每张 <img> 打上 `data-page="<filename>"`，供 `useScrollSync` 找左右对齐锚点。
 * scrollRef 暴露给父组件，用于拿 "可滚动容器"（即 .source-images-list）句柄。
 */

import { forwardRef, useState } from "react";

import { getSourceImageUrl } from "../api/client";
import { useTranslation } from "../i18n";

interface SourceImagePanelProps {
  readonly taskId: string;
  readonly images: readonly string[];
}

export const SourceImagePanel = forwardRef<
  HTMLDivElement,
  SourceImagePanelProps
>(function SourceImagePanel(
  { taskId, images },
  scrollRef,
): React.JSX.Element {
  const { t } = useTranslation();
  const [lightboxSrc, setLightboxSrc] = useState<string | undefined>();

  return (
    <div className="preview-source-images">
      <h4>{t("sourceImages.title")}</h4>
      <div ref={scrollRef} className="source-images-list">
        {images.map((name) => {
          const src = getSourceImageUrl(taskId, name);
          // data-page 用图片"裸文件名"（不带子目录前缀），与后端
          // PageDeduplicator 插入的 <!-- page: xxx --> 标记对齐。
          const pageKey = name.split("/").pop() ?? name;
          return (
            <img
              key={name}
              src={src}
              alt={name}
              title={name}
              data-page={pageKey}
              className="source-image-item"
              onClick={() => { setLightboxSrc(src); }}
            />
          );
        })}
      </div>

      {/* 点击放大遮罩 */}
      {lightboxSrc !== undefined && (
        <div
          className="image-lightbox"
          onClick={() => { setLightboxSrc(undefined); }}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Escape") setLightboxSrc(undefined);
          }}
        >
          <img src={lightboxSrc} alt={t("sourceImages.lightboxAlt")} />
        </div>
      )}
    </div>
  );
});
