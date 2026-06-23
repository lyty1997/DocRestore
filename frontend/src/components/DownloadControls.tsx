/**
 * 下载控件：附加导出格式（Word / PDF）勾选 + 下载按钮（Epic D）。
 *
 * 导出在**下载环节**按需选择（不进任务表单）：勾选的格式经 `getDownloadUrl`
 * 拼成 `?formats=docx,pdf`，后端把 `document.md` 导出成对应格式一并打进 zip；
 * 不勾则纯 markdown zip（行为不变）。详见 docs/zh/export-mode.md §8。
 */

import { useState } from "react";

import { getDownloadUrl } from "../api/client";
import { useTranslation } from "../i18n";
import type { TranslationKey } from "../i18n/zh-CN";

/** 当前支持的附加导出格式（与后端 SUPPORTED_FORMATS 对齐） */
const EXPORT_FORMATS = ["docx", "pdf", "xlsx", "pptx"] as const;
type ExportFormat = (typeof EXPORT_FORMATS)[number];

/** 格式 → i18n 标签 key（显式映射，保持 t() 键的类型安全） */
const FORMAT_LABEL_KEY: Record<ExportFormat, TranslationKey> = {
  docx: "taskResult.exportFormat_docx",
  pdf: "taskResult.exportFormat_pdf",
  xlsx: "taskResult.exportFormat_xlsx",
  pptx: "taskResult.exportFormat_pptx",
};

interface DownloadControlsProps {
  /** 目标任务 ID */
  readonly taskId: string;
  /** 下载按钮文案 key（TaskResult / TaskDetail 各用自己的命名空间） */
  readonly downloadLabelKey: TranslationKey;
}

export function DownloadControls({
  taskId,
  downloadLabelKey,
}: DownloadControlsProps): React.JSX.Element {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<ReadonlySet<ExportFormat>>(
    () => new Set(),
  );

  const toggle = (fmt: ExportFormat): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(fmt)) next.delete(fmt);
      else next.add(fmt);
      return next;
    });
  };

  const downloadUrl = getDownloadUrl(taskId, [...selected]);

  return (
    <div className="download-controls">
      <span className="download-export-label">
        {t("taskResult.exportLabel")}
      </span>
      {EXPORT_FORMATS.map((fmt) => (
        <label key={fmt} className="download-export-option">
          <input
            type="checkbox"
            checked={selected.has(fmt)}
            onChange={() => {
              toggle(fmt);
            }}
          />
          {t(FORMAT_LABEL_KEY[fmt])}
        </label>
      ))}
      <a href={downloadUrl} download className="download-btn">
        {t(downloadLabelKey)}
      </a>
    </div>
  );
}
