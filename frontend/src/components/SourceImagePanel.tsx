/**
 * 源图片面板：展示任务的原始输入图片，支持点击放大查看。
 *
 * 每张 <img> 打上 `data-page="<filename>"`，供预览同步滚动找左右对齐锚点。
 * scrollRef 暴露给父组件，用于拿 "可滚动容器"（即 .source-images-list）句柄。
 */

import { forwardRef } from "react";

import type { LayoutPagePayload } from "../api/schemas";
import type { SourceImageHighlight } from "../features/task/blockHighlight";
import { imageNameToListItem } from "../features/task/sourceImagePreview";
import { useTranslation } from "../i18n";
import { SourceImageList } from "./SourceImageList";

interface SourceImagePanelProps {
  readonly taskId: string;
  readonly images: readonly string[];
  /** 光标块 bbox 高亮（Epic E）；仅文档编辑模式传入，缺省不高亮。 */
  readonly highlight?: SourceImageHighlight | undefined;
  /** 处理图高亮：true 时源图栏改显处理图（PPT 矫正 / content_crop 裁剪，§13/§15）；
   *  缺省显原图。 */
  readonly processed?: boolean;
  /** 多文档相对子目录，构造处理图 URL 用。 */
  readonly docDir?: string | undefined;
  /** 版面全览（E8）：各页全部块 + 是否显示叠加层；缺省不显示。 */
  readonly layoutPages?: readonly LayoutPagePayload[] | undefined;
  readonly showOverlay?: boolean;
}

export const SourceImagePanel = forwardRef<
  HTMLDivElement,
  SourceImagePanelProps
>(function SourceImagePanel(
  { taskId, images, highlight, processed = false, docDir, layoutPages,
    showOverlay = false },
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
        highlight={highlight}
        processed={processed}
        docDir={docDir}
        layoutPages={layoutPages}
        showOverlay={showOverlay}
      />
    </div>
  );
});
