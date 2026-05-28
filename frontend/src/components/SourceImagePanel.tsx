/**
 * 源图片面板：展示任务的原始输入图片，支持点击放大查看。
 *
 * 每张 <img> 打上 `data-page="<filename>"`，供预览同步滚动找左右对齐锚点。
 * scrollRef 暴露给父组件，用于拿 "可滚动容器"（即 .source-images-list）句柄。
 */

import { forwardRef } from "react";

import { imageNameToListItem } from "../features/task/sourceImagePreview";
import { useTranslation } from "../i18n";
import { SourceImageList } from "./SourceImageList";

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

  return (
    <div className="preview-source-images">
      <h4>{t("sourceImages.title")}</h4>
      <SourceImageList
        ref={scrollRef}
        taskId={taskId}
        images={images.map((name) => imageNameToListItem(name))}
        listClassName="source-images-list"
        imageClassName="source-image-item"
      />
    </div>
  );
});
