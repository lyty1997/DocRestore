/**
 * 正文裁剪预览面板（文档模式）：拉取 image_dir 的建议框 → 缩略图条 / 左右
 * 切换键选图 → 单张 CropEditor 拖拽微调 → 把最终框（图名 → 框）与删除
 * （任务级排除）清单上报给 TaskForm，提交时分别作为 crop_boxes 与
 * ocr.exclude_images。
 *
 * - 列出**全部**图（含未检测到侧栏的）：无框图只预览不裁剪，但同样可删除；
 * - 同屏只挂一个编辑器：多图全量渲染会把表单撑出上万像素；
 * - "删除"是任务级排除（跳过处理），绝不动磁盘上的源文件，可随时恢复。
 */

import { useEffect, useRef, useState } from "react";

import { detectCropBoxes, getCropImageUrl } from "../api/client";
import type { CropBox, CropDetectItem } from "../api/schemas";
import { useTranslation } from "../i18n";
import { CropEditor } from "./CropEditor";
import {
  CropZoomViewport,
  type CropZoomViewportHandle,
} from "./CropZoomViewport";

interface CropPanelProps {
  readonly imageDir: string;
  readonly enabled: boolean;
  /** 框变化时上报（图名 → 框）；提交时 TaskForm 作为 crop_boxes 传给后端。 */
  readonly onBoxesChange: (boxes: Record<string, CropBox>) => void;
  /** 删除（任务级排除）清单变化时上报；提交时作为 ocr.exclude_images。 */
  readonly onExcludeChange: (names: readonly string[]) => void;
}

export function CropPanel({
  imageDir,
  enabled,
  onBoxesChange,
  onExcludeChange,
}: CropPanelProps): React.JSX.Element | undefined {
  const { t } = useTranslation();
  const [items, setItems] = useState<readonly CropDetectItem[]>([]);
  const [boxes, setBoxes] = useState<Record<string, CropBox>>({});
  const [excluded, setExcluded] = useState<readonly string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const thumbsRef = useRef<HTMLDivElement>(null);
  // 缩放视口句柄：拖拽松手后按最新框重新落位（图随框缩放铺开）
  const zoomRef = useRef<CropZoomViewportHandle>(null);

  // 切图（侧边键 / 缩略图）后把激活缩略图滚进可视区，条与编辑器保持对应
  useEffect(() => {
    const active = thumbsRef.current?.querySelector(".crop-thumb.active");
    active?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selected]);

  useEffect(() => {
    if (!enabled || imageDir.trim() === "") {
      setItems([]);
      setBoxes({});
      setExcluded([]);
      setSelected("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    const run = async (): Promise<void> => {
      try {
        const resp = await detectCropBoxes(imageDir);
        if (cancelled) return;
        const init: Record<string, CropBox> = {};
        for (const it of resp.images) {
          if (it.box !== null) init[it.name] = it.box;
        }
        setItems(resp.images);
        setBoxes(init);
        setExcluded([]);
        setSelected(resp.images[0]?.name ?? "");
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return (): void => {
      cancelled = true;
    };
  }, [enabled, imageDir]);

  // 框 / 排除清单变化统一上报；被删除图的框不上报（不该预裁剪它）
  useEffect(() => {
    if (!enabled) {
      onBoxesChange({});
      return;
    }
    const kept = Object.fromEntries(
      Object.entries(boxes).filter(([name]) => !excluded.includes(name)),
    );
    onBoxesChange(kept);
  }, [boxes, excluded, enabled, onBoxesChange]);

  useEffect(() => {
    onExcludeChange(enabled ? excluded : []);
  }, [excluded, enabled, onExcludeChange]);

  if (!enabled) return undefined;

  const updateBox = (name: string, box: CropBox): void => {
    setBoxes((prev) => ({ ...prev, [name]: box }));
  };

  // 无检测框的图手动开启裁剪：初始框居中 80% 宽 × 整高（与检测框同形态）
  const addManualBox = (it: CropDetectItem): void => {
    updateBox(it.name, {
      x0: Math.round(it.width * 0.1),
      y0: 0,
      x1: Math.round(it.width * 0.9),
      y1: it.height,
    });
  };

  // 取消该图裁剪（检测误检 / 手动框后悔），回到"不裁剪"状态
  const removeBox = (name: string): void => {
    setBoxes((prev) => {
      const next = { ...prev };
      delete next[name];  // eslint-disable-line @typescript-eslint/no-dynamic-delete
      return next;
    });
  };

  const visible = items.filter((it) => !excluded.includes(it.name));
  const currentIdx = visible.findIndex((it) => it.name === selected);
  const current = currentIdx === -1 ? undefined : visible[currentIdx];
  const currentBox = current === undefined ? undefined : boxes[current.name];

  // 上一张 / 下一张（到边界禁用，不回绕）
  const goTo = (delta: number): void => {
    const next = visible[currentIdx + delta];
    if (next !== undefined) setSelected(next.name);
  };

  // 删除当前图（任务级排除）：选中位移到下一张（末张则前移）
  const removeCurrent = (): void => {
    if (current === undefined) return;
    const fallback = visible[currentIdx + 1] ?? visible[currentIdx - 1];
    setExcluded((prev) => [...prev, current.name]);
    setSelected(fallback?.name ?? "");
  };

  const restore = (name: string): void => {
    setExcluded((prev) => prev.filter((n) => n !== name));
    setSelected(name);
  };

  return (
    <div className="crop-panel">
      <p className="crop-panel-hint">{t("crop.hint")}</p>
      {loading && <p className="crop-panel-hint">{t("crop.detecting")}</p>}
      {failed && <p className="crop-panel-error">{t("crop.detectFailed")}</p>}
      {!loading && !failed && items.length === 0 && (
        <p className="crop-panel-hint">{t("crop.noneToCrop")}</p>
      )}

      {visible.length > 0 && (
        <div className="crop-thumbs" role="listbox" ref={thumbsRef}>
          {visible.map((it) => (
            <button
              type="button"
              key={it.name}
              role="option"
              aria-selected={it.name === selected}
              className={`crop-thumb${it.name === selected ? " active" : ""}`}
              onClick={() => {
                setSelected(it.name);
              }}
              title={it.name}
            >
              {/* lazy：多图时只加载滚进视口的缩略图 */}
              <img
                src={getCropImageUrl(imageDir, it.name)}
                alt={it.name}
                loading="lazy"
              />
              <span className="crop-thumb-name">{it.name}</span>
            </button>
          ))}
        </div>
      )}

      {excluded.length > 0 && (
        <p className="crop-excluded">
          {t("crop.excludedLabel", { count: excluded.length })}
          {excluded.map((name) => (
            <button
              type="button"
              key={name}
              className="crop-excluded-item"
              onClick={() => {
                restore(name);
              }}
            >
              {name}
            </button>
          ))}
        </p>
      )}

      {current !== undefined && (
        <div className="crop-panel-item">
          <div className="crop-panel-nav">
            <div className="crop-panel-name">
              {`${(currentIdx + 1).toString()} / ${visible.length.toString()} · ${current.name}`}
            </div>
            {currentBox !== undefined && (
              <button
                type="button"
                className="crop-action-btn"
                onClick={() => {
                  removeBox(current.name);
                }}
                title={t("crop.removeBox")}
              >
                {t("crop.removeBox")}
              </button>
            )}
            <button
              type="button"
              className="crop-delete-btn"
              onClick={removeCurrent}
              title={t("crop.delete")}
            >
              {`✕ ${t("crop.delete")}`}
            </button>
          </div>
          <div className="crop-stage">
            <button
              type="button"
              className="crop-side-btn"
              onClick={() => {
                goTo(-1);
              }}
              disabled={currentIdx <= 0}
              title={t("crop.prev")}
              aria-label={t("crop.prev")}
            >
              ‹
            </button>
            <div className="crop-stage-main">
              {currentBox === undefined ? (
                <>
                  {/* 未检测到侧栏：默认不裁剪，可手动框选开启人工裁剪 */}
                  <img
                    className="crop-plain-img"
                    src={getCropImageUrl(imageDir, current.name)}
                    alt={current.name}
                  />
                  <p className="crop-panel-hint">
                    {t("crop.noBoxHint")}
                    <button
                      type="button"
                      className="crop-action-btn"
                      onClick={() => {
                        addManualBox(current);
                      }}
                    >
                      {t("crop.addBox")}
                    </button>
                  </p>
                </>
              ) : (
                <CropZoomViewport
                  key={current.name}
                  ref={zoomRef}
                  className="crop-panel-viewport"
                  naturalWidth={current.width}
                  naturalHeight={current.height}
                  initialRegion={currentBox}
                >
                  <CropEditor
                    imageUrl={getCropImageUrl(imageDir, current.name)}
                    naturalWidth={current.width}
                    naturalHeight={current.height}
                    box={currentBox}
                    onChange={(b): void => {
                      updateBox(current.name, b);
                    }}
                    onDragEnd={(): void => {
                      const b = boxes[current.name];
                      if (b !== undefined) zoomRef.current?.refit(b);
                    }}
                  />
                </CropZoomViewport>
              )}
            </div>
            <button
              type="button"
              className="crop-side-btn"
              onClick={() => {
                goTo(1);
              }}
              disabled={currentIdx >= visible.length - 1}
              title={t("crop.next")}
              aria-label={t("crop.next")}
            >
              ›
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
