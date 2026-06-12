/**
 * 裁剪缩放视口（FigureCropDialog 重截插图 与 CropPanel 输入图裁剪 共用）。
 *
 * 编辑器放进固定尺寸视口（overflow hidden）：内容层按"整图等比适配视口"的
 * 基准尺寸布局，外层在拖拽松手 / 切模式时调 ``refit(region)``，按裁剪框区域
 * 重算 translate+scale（cropFit 纯几何，铺满视口约 78%），CSS transition
 * 平滑过渡；同时写入 ``--crop-zoom`` 供手柄 / 框线反向缩放保持视觉尺寸。
 *
 * 切换源图 / 图片时由外层换 ``key`` 强制重挂，``initialRegion`` 仅在挂载时
 * 落位一次；窗口 resize 按最近一次区域自动重算。
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import {
  fitRegion,
  type RegionBBox,
  type ViewTransform,
} from "../features/task/cropFit";

interface CropZoomViewportProps {
  readonly naturalWidth: number;
  readonly naturalHeight: number;
  /** 挂载时的初始落位区域（之后的联动经 handle.refit 显式触发）。 */
  readonly initialRegion?: RegionBBox | undefined;
  /** 视口附加 class（覆盖高度等场景差异）。 */
  readonly className?: string | undefined;
  readonly children: React.ReactNode;
}

/** 命令式句柄：外层在拖拽松手 / 切模式时按最新区域重新落位。 */
export interface CropZoomViewportHandle {
  readonly refit: (region: RegionBBox) => void;
}

export const CropZoomViewport = forwardRef<
  CropZoomViewportHandle,
  CropZoomViewportProps
>(function CropZoomViewport(
  { naturalWidth, naturalHeight, initialRegion, className, children },
  ref,
): React.JSX.Element {
  const viewportRef = useRef<HTMLDivElement>(null);
  const lastRegionRef = useRef<RegionBBox | undefined>(initialRegion);
  // undefined = 未测量 / 无布局环境（jsdom），回退整图 100% 宽展示
  const [view, setView] = useState<ViewTransform | undefined>();

  const refit = useCallback(
    (region: RegionBBox): void => {
      const viewport = viewportRef.current;
      if (viewport === null) return;
      lastRegionRef.current = region;
      setView(fitRegion(
        viewport.clientWidth,
        viewport.clientHeight,
        naturalWidth,
        naturalHeight,
        region,
      ));
    },
    [naturalWidth, naturalHeight],
  );

  useImperativeHandle(ref, () => ({ refit }), [refit]);

  // 挂载后按初始区域落位（切图由外层换 key 重挂，故只需一次）
  useEffect(() => {
    if (lastRegionRef.current !== undefined) refit(lastRegionRef.current);
  }, [refit]);

  // 窗口尺寸变化按最近一次区域重算
  useEffect(() => {
    const onResize = (): void => {
      if (lastRegionRef.current !== undefined) refit(lastRegionRef.current);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, [refit]);

  return (
    <div
      ref={viewportRef}
      className={`figure-crop-viewport${className === undefined ? "" : ` ${className}`}`}
    >
      <div
        className="figure-crop-zoom"
        style={
          view === undefined
            ? undefined
            : {
                width: view.baseWidth,
                height: view.baseHeight,
                transform: `translate(${view.tx.toString()}px, ${view.ty.toString()}px) scale(${view.zoom.toString()})`,
                // 手柄 / 框线按此反向缩放，视觉尺寸不随 zoom 变大
                "--crop-zoom": view.zoom,
              }
        }
      >
        {children}
      </div>
    </div>
  );
});

CropZoomViewport.displayName = "CropZoomViewport";
