/**
 * 图片放大遮罩。点击或按 Esc 关闭；src=undefined 时不渲染。
 *
 * 抽出来供 SourceImagePanel / CodeViewer 等多个图片列表组件复用。
 *
 * Esc 关闭：本地 keydown 监听需要焦点。挂载时把 focus 移到 wrapper 上，
 * 配合 ``tabIndex={0}`` 让组件自带键盘可达性。
 */

import { useEffect, useRef } from "react";

import { useTranslation } from "../i18n";

interface ImageLightboxProps {
  readonly src: string | undefined;
  readonly onClose: () => void;
}

export function ImageLightbox({
  src,
  onClose,
}: ImageLightboxProps): React.JSX.Element | undefined {
  const { t } = useTranslation();
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (src !== undefined) wrapperRef.current?.focus();
  }, [src]);

  if (src === undefined) return undefined;
  return (
    <div
      ref={wrapperRef}
      className="image-lightbox"
      onClick={onClose}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <img src={src} alt={t("sourceImages.lightboxAlt")} />
    </div>
  );
}
