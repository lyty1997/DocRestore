/**
 * 手动重截插图对话框（编辑模式用）。
 *
 * 流程：列出任务源图 → 下拉选一张 → 在 CropEditor 上框选插图区域 → 确认
 * 调用后端 ``crop-figure`` 裁出 → 回调 asset_path，由编辑器插入图片。
 *
 * 复用 ``CropEditor``（与建任务前"正文裁剪预览"同一个拖拽/缩放框组件）。
 *
 * 缩放联动：编辑器放在固定尺寸视口里，每次拖拽松手后按当前裁剪框（quad 模式
 * 取四点外接框）重算 translate+scale，原图平滑缩放铺开到视口约 78%——框越小
 * 图放得越大，单一画面即所见即所得，无独立预览窗。
 */

import { useEffect, useRef, useState } from "react";

import { cropFigure, getSourceImageUrl, listSourceImages } from "../api/client";
import type { CropBox, CropQuad } from "../api/schemas";
import { quadBBox, type RegionBBox } from "../features/task/cropFit";
import { useTranslation } from "../i18n";
import { CropEditor } from "./CropEditor";
import {
  CropZoomViewport,
  type CropZoomViewportHandle,
} from "./CropZoomViewport";
import { QuadCropEditor } from "./QuadCropEditor";

interface NaturalSize {
  readonly width: number;
  readonly height: number;
}

/** 裁剪模式：矩形框 / 四角透视校正。 */
type CropMode = "rect" | "quad";

/** 由矩形框生成初始四角（左上/右上/右下/左下）。 */
function boxToQuad(box: CropBox): CropQuad {
  return {
    tl: { x: box.x0, y: box.y0 },
    tr: { x: box.x1, y: box.y0 },
    br: { x: box.x1, y: box.y1 },
    bl: { x: box.x0, y: box.y1 },
  };
}

interface FigureCropDialogProps {
  readonly taskId: string;
  readonly docDir?: string | undefined;
  /** 光标所在页的原图文件名（来自 ``<!-- page: X -->`` 标记），用于自动选源图。 */
  readonly cursorPage?: string | undefined;
  /** 确认裁剪后回调 markdown 相对引用（images/manual_N.jpg）。 */
  readonly onConfirm: (assetPath: string) => void;
  readonly onClose: () => void;
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** 取路径最后一段（源图列表是相对路径，页标记只含基名）。 */
function basename(path: string): string {
  return path.split("/").at(-1) ?? path;
}

/**
 * 按光标所在页（原图基名）在源图列表里挑最匹配的一张。
 *
 * 源图列表是相对 ``image_dir`` 的路径（多文档任务带子目录前缀），页标记
 * 只含基名 → 按基名匹配；若有多张同名（多文档），优先取 ``docDir`` 下那张。
 * 无匹配返回 ``undefined``，由调用方回退到列表首张。
 */
function matchSourceByPage(
  sources: readonly string[],
  cursorPage: string | undefined,
  docDir: string | undefined,
): string | undefined {
  if (cursorPage === undefined || cursorPage === "") return undefined;
  const target = basename(cursorPage);
  const candidates = sources.filter((s) => basename(s) === target);
  if (candidates.length === 0) return undefined;
  if (docDir !== undefined && docDir !== "") {
    const scoped = candidates.find((s) => s.startsWith(`${docDir}/`));
    if (scoped !== undefined) return scoped;
  }
  return candidates[0];
}

export function FigureCropDialog({
  taskId,
  docDir,
  cursorPage,
  onConfirm,
  onClose,
}: FigureCropDialogProps): React.JSX.Element {
  const { t } = useTranslation();
  const [sources, setSources] = useState<readonly string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [natural, setNatural] = useState<NaturalSize | undefined>();
  const [box, setBox] = useState<CropBox | undefined>();
  const [quad, setQuad] = useState<CropQuad | undefined>();
  const [mode, setMode] = useState<CropMode>("rect");
  const [loadingList, setLoadingList] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | undefined>();
  // 当前选中的源图是否由光标所在页自动锚定（用于提示；用户改选后清除）
  const [autoMatched, setAutoMatched] = useState<boolean>(false);
  // 缩放视口句柄：拖拽松手 / 切模式时按最新区域重新落位
  const zoomRef = useRef<CropZoomViewportHandle>(null);

  // 当前模式下驱动缩放联动的区域（quad 模式取四点外接框）
  const activeRegion = (): RegionBBox | undefined =>
    mode === "quad"
      ? (quad === undefined ? undefined : quadBBox(quad))
      : box;

  const onDragEnd = (): void => {
    const region = activeRegion();
    if (region !== undefined) zoomRef.current?.refit(region);
  };

  useEffect(() => {
    let cancelled = false;
    listSourceImages(taskId)
      .then((res) => {
        if (cancelled) return;
        setSources(res.images);
        // 优先锚定到光标所在页的源图，无匹配则回退列表首张
        const matched = matchSourceByPage(res.images, cursorPage, docDir);
        setSelected(matched ?? res.images[0] ?? "");
        setAutoMatched(matched !== undefined);
        setLoadingList(false);
      })
      .catch((error_: unknown) => {
        if (cancelled) return;
        setError(errMessage(error_));
        setLoadingList(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, cursorPage, docDir]);

  // 切换源图（事件里重置尺寸 / 框，等新图 onLoad 重新初始化；不放 effect 避免级联渲染）
  const selectSource = (name: string): void => {
    setSelected(name);
    setNatural(undefined);
    setBox(undefined);
    setQuad(undefined);
    setAutoMatched(false);
  };

  const onImgLoad = (e: React.SyntheticEvent<HTMLImageElement>): void => {
    const img = e.currentTarget;
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    if (w <= 0 || h <= 0) return;
    setNatural({ width: w, height: h });
    // 初始框：居中 60%，用户再微调框住插图；四角同步用框的角初始化
    const bw = Math.round(w * 0.6);
    const bh = Math.round(h * 0.6);
    const x0 = Math.round((w - bw) / 2);
    const y0 = Math.round((h - bh) / 2);
    const initBox: CropBox = { x0, y0, x1: x0 + bw, y1: y0 + bh };
    setBox(initBox);
    setQuad(boxToQuad(initBox));
    // 初始落位由 CropZoomViewport 的 initialRegion 在挂载时完成
  };

  // 切模式：进四角校正时用当前矩形框作初始四角（用户在此基础上把角拖到插图实际四角）
  // 两个方向切换后的活动区域都是当前矩形框 → 按它重算视图
  const switchMode = (next: CropMode): void => {
    if (next === "quad" && box !== undefined) setQuad(boxToQuad(box));
    setMode(next);
    if (box !== undefined) zoomRef.current?.refit(box);
  };

  const onSubmit = (): void => {
    if (selected === "") return;
    // 当前模式所需区域未就绪则不提交（quad 模式要 quad，rect 模式要 box）
    if (mode === "quad" ? quad === undefined : box === undefined) return;
    setSubmitting(true);
    setError(undefined);
    // body 字段 box/quad 均可选；上面的守卫保证当前模式对应字段已就绪
    const body =
      mode === "quad"
        ? { source_filename: selected, quad, doc_dir: docDir }
        : { source_filename: selected, box, doc_dir: docDir };
    cropFigure(taskId, body)
      .then((res) => {
        onConfirm(res.asset_path);
      })
      .catch((error_: unknown) => {
        setError(errMessage(error_));
        setSubmitting(false);
      });
  };

  const ready =
    natural !== undefined
    && (mode === "quad" ? quad !== undefined : box !== undefined);

  return (
    <div className="figure-crop-overlay" role="dialog" aria-modal="true">
      <div className="figure-crop-dialog">
        <div className="figure-crop-header">
          <h3>{t("figureCrop.title")}</h3>
          <button
            type="button"
            className="figure-crop-close"
            onClick={onClose}
            title={t("common.close")}
          >
            ×
          </button>
        </div>
        <p className="figure-crop-hint">{t("figureCrop.hint")}</p>

        {loadingList && (
          <p className="figure-crop-status">{t("figureCrop.loading")}</p>
        )}
        {!loadingList && sources.length === 0 && (
          <p className="figure-crop-status">{t("figureCrop.noSources")}</p>
        )}

        {sources.length > 0 && (
          <label className="figure-crop-select">
            <span>{t("figureCrop.sourceLabel")}</span>
            <select
              value={selected}
              onChange={(e) => {
                selectSource(e.target.value);
              }}
            >
              {sources.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}

        {autoMatched && (
          <p className="figure-crop-auto">{t("figureCrop.fromCursorPage")}</p>
        )}

        {sources.length > 0 && (
          <div className="figure-crop-mode" role="group">
            <button
              type="button"
              className={`toggle-btn ${mode === "rect" ? "active" : ""}`}
              onClick={() => { switchMode("rect"); }}
            >
              {t("figureCrop.modeRect")}
            </button>
            <button
              type="button"
              className={`toggle-btn ${mode === "quad" ? "active" : ""}`}
              onClick={() => { switchMode("quad"); }}
            >
              {t("figureCrop.modeQuad")}
            </button>
          </div>
        )}

        {mode === "quad" && sources.length > 0 && (
          <p className="figure-crop-hint">{t("figureCrop.quadHint")}</p>
        )}

        {selected !== "" && (
          <>
            {/* 测量用隐藏图：拿原图自然尺寸后才渲染编辑器 */}
            <img
              src={getSourceImageUrl(taskId, selected)}
              alt=""
              style={{ display: "none" }}
              onLoad={onImgLoad}
            />
            {ready ? (
              <CropZoomViewport
                key={selected}
                ref={zoomRef}
                naturalWidth={natural.width}
                naturalHeight={natural.height}
                initialRegion={box}
              >
                {mode === "rect" && box !== undefined && (
                  <CropEditor
                    imageUrl={getSourceImageUrl(taskId, selected)}
                    naturalWidth={natural.width}
                    naturalHeight={natural.height}
                    box={box}
                    onChange={setBox}
                    onDragEnd={onDragEnd}
                  />
                )}
                {mode === "quad" && quad !== undefined && (
                  <QuadCropEditor
                    imageUrl={getSourceImageUrl(taskId, selected)}
                    naturalWidth={natural.width}
                    naturalHeight={natural.height}
                    quad={quad}
                    onChange={setQuad}
                    onDragEnd={onDragEnd}
                  />
                )}
              </CropZoomViewport>
            ) : (
              <div className="figure-crop-viewport" />
            )}
          </>
        )}

        {error !== undefined && (
          <p className="figure-crop-error">{error}</p>
        )}

        <div className="figure-crop-footer">
          <button type="button" onClick={onClose} disabled={submitting}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="primary"
            onClick={onSubmit}
            disabled={!ready || submitting}
          >
            {submitting ? t("figureCrop.submitting") : t("figureCrop.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
