/**
 * 手动重截插图对话框（编辑模式用）。
 *
 * 流程：列出任务源图 → 下拉选一张 → 在 CropEditor 上框选插图区域 → 确认
 * 调用后端 ``crop-figure`` 裁出 → 回调 asset_path，由编辑器插入图片。
 *
 * 复用 ``CropEditor``（与建任务前"正文裁剪预览"同一个拖拽/缩放框组件）。
 */

import { useEffect, useState } from "react";

import { cropFigure, getSourceImageUrl, listSourceImages } from "../api/client";
import type { CropBox } from "../api/schemas";
import { useTranslation } from "../i18n";
import { CropEditor } from "./CropEditor";

interface NaturalSize {
  readonly width: number;
  readonly height: number;
}

interface FigureCropDialogProps {
  readonly taskId: string;
  readonly docDir?: string | undefined;
  /** 确认裁剪后回调 markdown 相对引用（images/manual_N.jpg）。 */
  readonly onConfirm: (assetPath: string) => void;
  readonly onClose: () => void;
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function FigureCropDialog({
  taskId,
  docDir,
  onConfirm,
  onClose,
}: FigureCropDialogProps): React.JSX.Element {
  const { t } = useTranslation();
  const [sources, setSources] = useState<readonly string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [natural, setNatural] = useState<NaturalSize | undefined>();
  const [box, setBox] = useState<CropBox | undefined>();
  const [loadingList, setLoadingList] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | undefined>();

  useEffect(() => {
    let cancelled = false;
    listSourceImages(taskId)
      .then((res) => {
        if (cancelled) return;
        setSources(res.images);
        setSelected(res.images[0] ?? "");
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
  }, [taskId]);

  // 切换源图（事件里重置尺寸 / 框，等新图 onLoad 重新初始化；不放 effect 避免级联渲染）
  const selectSource = (name: string): void => {
    setSelected(name);
    setNatural(undefined);
    setBox(undefined);
  };

  const onImgLoad = (e: React.SyntheticEvent<HTMLImageElement>): void => {
    const img = e.currentTarget;
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    if (w <= 0 || h <= 0) return;
    setNatural({ width: w, height: h });
    // 初始框：居中 60%，用户再微调框住插图
    const bw = Math.round(w * 0.6);
    const bh = Math.round(h * 0.6);
    const x0 = Math.round((w - bw) / 2);
    const y0 = Math.round((h - bh) / 2);
    setBox({ x0, y0, x1: x0 + bw, y1: y0 + bh });
  };

  const onSubmit = (): void => {
    if (selected === "" || box === undefined) return;
    setSubmitting(true);
    setError(undefined);
    cropFigure(taskId, { source_filename: selected, box, doc_dir: docDir })
      .then((res) => {
        onConfirm(res.asset_path);
      })
      .catch((error_: unknown) => {
        setError(errMessage(error_));
        setSubmitting(false);
      });
  };

  const ready = natural !== undefined && box !== undefined;

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

        {selected !== "" && (
          <div className="figure-crop-canvas">
            {/* 测量用隐藏图：拿原图自然尺寸后才渲染 CropEditor */}
            <img
              src={getSourceImageUrl(taskId, selected)}
              alt=""
              style={{ display: "none" }}
              onLoad={onImgLoad}
            />
            {ready && (
              <CropEditor
                imageUrl={getSourceImageUrl(taskId, selected)}
                naturalWidth={natural.width}
                naturalHeight={natural.height}
                box={box}
                onChange={setBox}
              />
            )}
          </div>
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
