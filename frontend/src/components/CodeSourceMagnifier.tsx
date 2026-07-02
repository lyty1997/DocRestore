/**
 * 代码模式源图放大镜（#93 · F3）。
 *
 * 嵌在 IDE 编辑栏顶部：把当前悬停行对应的源图局部放大铺满（复用 CropZoomViewport
 * 纯 CSS 缩放），并用 BlockHighlightOverlay 在放大视图里描出当前行 bbox。无目标 /
 * 无图 → 显占位提示。源图自然尺寸由探测 ``<img onLoad>`` 落地（不依赖外部传尺寸，
 * 避免 decode race）；同图内换行经命令式 ``refit`` 平滑过渡到新区域。
 *
 * ``processed``（§14.1）：代码模式手动裁剪任务的行 bbox 在**裁剪图坐标系**，须改显
 * 处理图（``.content_crop/{stem}_crop``）才对齐；该页无处理图（404）时 ``onError`` 回退
 * 原图（未裁剪页 bbox 本在原图系，逐页混合自洽），与文档 SourceImageList 同口径。
 */

import { useEffect, useRef, useState } from "react";

import { getProcessedImageUrl, getSourceImageUrl } from "../api/client";
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
  /** 手动裁剪任务：行 bbox 在裁剪图坐标系，改显处理图对齐（§14.1）；缺省显原图。 */
  readonly processed?: boolean;
  /** 多文档相对子目录，构造处理图 URL 用（代码单根留空）。 */
  readonly docDir?: string | undefined;
}

export function CodeSourceMagnifier({
  taskId,
  target,
  image,
  hint,
  processed = false,
  docDir,
}: CodeSourceMagnifierProps): React.JSX.Element {
  const viewportRef = useRef<CropZoomViewportHandle>(null);
  // 自然尺寸与其来源 src 绑定：换图时派生值自动失效，无需 reset effect。
  const [naturalState, setNaturalState] = useState<
    { readonly src: string; readonly w: number; readonly h: number } | undefined
  >();
  // 处理图 404 回退：记录已回退过的 preferredSrc（按其值绑定，换页/换图自动失效，
  // 无 stale 标志——不复用命令式 dataset）。
  const [fellBack, setFellBack] = useState<string | undefined>();

  const originalSrc =
    image === undefined ? undefined : getSourceImageUrl(taskId, image.name);
  // 优先图：processed 且有图时取处理图（裁剪图坐标系与 bbox 对齐），否则回退原图
  // （?? 兜 undefined：未 processed / 无图时都落到 originalSrc）。
  const processedSrc =
    image !== undefined && processed
      ? getProcessedImageUrl(taskId, image.pageKey, docDir)
      : undefined;
  const preferredSrc = processedSrc ?? originalSrc;
  // 处理图已回退过 → 显原图（其 bbox 本在原图系，未裁剪页混合自洽）。
  const src =
    preferredSrc !== undefined && fellBack === preferredSrc
      ? originalSrc
      : preferredSrc;
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

  // 处理图探测失败 → 一次性回退原图（按 preferredSrc 绑定，非命令式 dataset）。
  const handleError =
    preferredSrc !== undefined &&
    src === preferredSrc &&
    preferredSrc !== originalSrc
      ? () => { setFellBack(preferredSrc); }
      : undefined;

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
          onError={handleError}
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
