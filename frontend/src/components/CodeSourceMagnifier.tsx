/**
 * 代码模式源图放大镜（#93 · F3）。
 *
 * 嵌在 IDE 编辑栏顶部：把当前悬停行对应的源图局部放大铺满（复用 CropZoomViewport
 * 纯 CSS 缩放），并用 BlockHighlightOverlay 在放大视图里描出当前行 bbox。无目标 /
 * 无图 → 显占位提示。源图自然尺寸由探测 ``<img onLoad>`` 落地（不依赖外部传尺寸，
 * 避免 decode race）；同图内换行经命令式 ``refit`` 平滑过渡到新区域。
 */

import { useEffect, useRef, useState } from "react";

import { getSourceImageUrl } from "../api/client";
import type { MagnifierTarget } from "../features/task/codeLineMagnifier";
import { BlockHighlightOverlay } from "./BlockHighlightOverlay";
import {
  CropZoomViewport,
  type CropZoomViewportHandle,
} from "./CropZoomViewport";

/** 已解析的源图标识（来自 CodeViewer 的 page→image 反查）。 */
export interface MagnifierImage {
  readonly name: string;
  readonly pageKey: string;
}

interface CodeSourceMagnifierProps {
  readonly taskId: string;
  /** 当前放大目标（页 + 区域 + 当前行 focus）；缺省显占位。 */
  readonly target: MagnifierTarget | undefined;
  /** target.page 反查到的源图；缺省显占位。 */
  readonly image: MagnifierImage | undefined;
  /** 占位提示文案（i18n）。 */
  readonly hint: string;
}

export function CodeSourceMagnifier({
  taskId,
  target,
  image,
  hint,
}: CodeSourceMagnifierProps): React.JSX.Element {
  const viewportRef = useRef<CropZoomViewportHandle>(null);
  // 自然尺寸与其来源 src 绑定：换图时派生值自动失效，无需 reset effect。
  const [naturalState, setNaturalState] = useState<
    { readonly src: string; readonly w: number; readonly h: number } | undefined
  >();

  const src =
    image === undefined ? undefined : getSourceImageUrl(taskId, image.name);
  const natural = naturalState?.src === src ? naturalState : undefined;

  // 同图内换行 / 目标变化 → 命令式 refit 到新区域（CropZoomViewport 仅挂载时落位一次）。
  useEffect(() => {
    if (natural !== undefined && target !== undefined) {
      viewportRef.current?.refit(target.region);
    }
  }, [natural, target]);

  if (target === undefined || image === undefined || src === undefined) {
    return (
      <div className="code-magnifier code-magnifier-empty">{hint}</div>
    );
  }

  return (
    <div className="code-magnifier">
      {natural === undefined ? (
        <img
          src={src}
          alt=""
          aria-hidden="true"
          className="code-magnifier-probe"
          onLoad={(event) => {
            const img = event.currentTarget;
            if (img.naturalWidth > 0 && img.naturalHeight > 0) {
              setNaturalState({
                src,
                w: img.naturalWidth,
                h: img.naturalHeight,
              });
            }
          }}
        />
      ) : (
        <CropZoomViewport
          ref={viewportRef}
          key={image.pageKey}
          className="code-magnifier-viewport"
          naturalWidth={natural.w}
          naturalHeight={natural.h}
          initialRegion={target.region}
        >
          <img
            src={src}
            alt={image.name}
            className="code-magnifier-img"
          />
          <BlockHighlightOverlay
            bbox={target.focus}
            imageSize={[natural.w, natural.h]}
          />
        </CropZoomViewport>
      )}
    </div>
  );
}
