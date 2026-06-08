/**
 * 正文裁剪预览面板（文档模式）：拉取 image_dir 的建议框 → 逐张图渲染 CropEditor
 * 供拖拽微调 → 把最终框（图名 → 框）上报给 TaskForm，提交时作为 crop_boxes。
 *
 * 只对"检测到框"的图显示编辑器（box=null 的已裁剪 / 无侧栏图跳过、不裁）。
 */

import { useEffect, useState } from "react";

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
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!enabled || imageDir.trim() === "") {
      setItems([]);
      setBoxes({});
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

  return (
    <div className="crop-panel">
      <p className="crop-panel-hint">{t("crop.hint")}</p>
      {loading && <p className="crop-panel-hint">{t("crop.detecting")}</p>}
      {failed && <p className="crop-panel-error">{t("crop.detectFailed")}</p>}
      {!loading && !failed && items.length === 0 && (
        <p className="crop-panel-hint">{t("crop.noneToCrop")}</p>
      )}
      {items.map((it) => {
        const box = boxes[it.name];
        if (box === undefined) return;
        return (
          <div key={it.name} className="crop-panel-item">
            <div className="crop-panel-name">{it.name}</div>
            <CropEditor
              imageUrl={getCropImageUrl(imageDir, it.name)}
              naturalWidth={it.width}
              naturalHeight={it.height}
              box={box}
              onChange={(b): void => {
                updateBox(it.name, b);
              }}
            />
          </div>
        );
      })}
    </div>
  );
}
