/**
 * 源图片面板：展示任务的原始输入图片，支持点击放大查看
 */

import { useState } from "react";

import { getSourceImageUrl } from "../api/client";
import { useTranslation } from "../i18n";

interface SourceImagePanelProps {
  readonly taskId: string;
  readonly images: readonly string[];
}

export function SourceImagePanel({
  taskId,
  images,
}: SourceImagePanelProps): React.JSX.Element {
  const { t } = useTranslation();
  const [lightboxSrc, setLightboxSrc] = useState<string | undefined>();

  return (
    <div className="preview-source-images">
      <h4>{t("sourceImages.title")}</h4>
      <div className="source-images-list">
        {images.map((name) => {
          const src = getSourceImageUrl(taskId, name);
          return (
            <img
              key={name}
              src={src}
              alt={name}
              title={name}
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
}
