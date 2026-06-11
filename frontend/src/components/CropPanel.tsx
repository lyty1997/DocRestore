/**
 * 正文裁剪预览面板（文档模式）：拉取 image_dir 的建议框 → 缩略图条选图 →
 * 单张 CropEditor 拖拽微调 → 把最终框（图名 → 框）上报给 TaskForm，提交时
 * 作为 crop_boxes。
 *
 * 只对"检测到框"的图显示（box=null 的已裁剪 / 无侧栏图跳过、不裁）。
 * 同屏只挂一个编辑器：多图时逐张全量渲染会把表单撑出上万像素，且每个编辑器
 * 的框外压暗叠加（见 .crop-editor 的 overflow 注释）；缩略图条横向滚动选图。
 */

import { useEffect, useRef, useState } from "react";

import { detectCropBoxes, getCropImageUrl } from "../api/client";
import type { CropBox, CropDetectItem } from "../api/schemas";
import { useTranslation } from "../i18n";
import { CropEditor } from "./CropEditor";

interface CropPanelProps {
  readonly imageDir: string;
  readonly enabled: boolean;
  /** 框变化时上报（图名 → 框）；提交时 TaskForm 作为 crop_boxes 传给后端。 */
  readonly onBoxesChange: (boxes: Record<string, CropBox>) => void;
}

export function CropPanel({
  imageDir,
  enabled,
  onBoxesChange,
}: CropPanelProps): React.JSX.Element | undefined {
  const { t } = useTranslation();
  const [items, setItems] = useState<readonly CropDetectItem[]>([]);
  const [boxes, setBoxes] = useState<Record<string, CropBox>>({});
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const thumbsRef = useRef<HTMLDivElement>(null);

  // 切图（按钮 / 缩略图）后把激活缩略图滚进可视区，条与编辑器保持对应
  useEffect(() => {
    const active = thumbsRef.current?.querySelector(".crop-thumb.active");
    active?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selected]);

  useEffect(() => {
    if (!enabled || imageDir.trim() === "") {
      setItems([]);
      setBoxes({});
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
        const withBox = resp.images.filter((it) => it.box !== null);
        const init: Record<string, CropBox> = {};
        for (const it of withBox) {
          if (it.box !== null) init[it.name] = it.box;
        }
        setItems(withBox);
        setBoxes(init);
        setSelected(withBox[0]?.name ?? "");
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

  // 框变化统一上报（含检测初始化与每次拖拽微调）。
  useEffect(() => {
    onBoxesChange(enabled ? boxes : {});
  }, [boxes, enabled, onBoxesChange]);

  if (!enabled) return undefined;

  const updateBox = (name: string, box: CropBox): void => {
    setBoxes((prev) => ({ ...prev, [name]: box }));
  };

  const currentIdx = items.findIndex((it) => it.name === selected);
  const current = currentIdx === -1 ? undefined : items[currentIdx];
  const currentBox = current === undefined ? undefined : boxes[current.name];

  // 上一张 / 下一张（到边界禁用，不回绕）
  const goTo = (delta: number): void => {
    const next = items[currentIdx + delta];
    if (next !== undefined) setSelected(next.name);
  };

  return (
    <div className="crop-panel">
      <p className="crop-panel-hint">{t("crop.hint")}</p>
      {loading && <p className="crop-panel-hint">{t("crop.detecting")}</p>}
      {failed && <p className="crop-panel-error">{t("crop.detectFailed")}</p>}
      {!loading && !failed && items.length === 0 && (
        <p className="crop-panel-hint">{t("crop.noneToCrop")}</p>
      )}

      {items.length > 0 && (
        <div className="crop-thumbs" role="listbox" ref={thumbsRef}>
          {items.map((it) => (
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

      {current !== undefined && currentBox !== undefined && (
        <div className="crop-panel-item">
          <div className="crop-panel-nav">
            <button
              type="button"
              className="crop-nav-btn"
              onClick={() => {
                goTo(-1);
              }}
              disabled={currentIdx <= 0}
              title={t("crop.prev")}
            >
              {`‹ ${t("crop.prev")}`}
            </button>
            <div className="crop-panel-name">
              {`${(currentIdx + 1).toString()} / ${items.length.toString()} · ${current.name}`}
            </div>
            <button
              type="button"
              className="crop-nav-btn"
              onClick={() => {
                goTo(1);
              }}
              disabled={currentIdx >= items.length - 1}
              title={t("crop.next")}
            >
              {`${t("crop.next")} ›`}
            </button>
          </div>
          <CropEditor
            key={current.name}
            imageUrl={getCropImageUrl(imageDir, current.name)}
            naturalWidth={current.width}
            naturalHeight={current.height}
            box={currentBox}
            onChange={(b): void => {
              updateBox(current.name, b);
            }}
          />
        </div>
      )}
    </div>
  );
}
