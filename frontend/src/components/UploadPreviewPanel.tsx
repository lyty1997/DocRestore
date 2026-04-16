import { useMemo, useState } from "react";

import type { UploadFileItem } from "../api/schemas";
import { useTranslation } from "../i18n";

interface UploadPreviewPanelProps {
  readonly files: readonly UploadFileItem[];
  readonly deletingFileIds: readonly string[];
  readonly onDelete: (fileId: string) => void;
}

interface UploadGroup {
  key: string;
  title: string;
  files: UploadFileItem[];
}

function groupFiles(
  files: readonly UploadFileItem[],
  ungroupedLabel: string,
): UploadGroup[] {
  const groups = new Map<string, UploadFileItem[]>();

  for (const file of files) {
    const slashIndex = file.relative_path.lastIndexOf("/");
    const key = slashIndex === -1 ? "ROOT" : file.relative_path.slice(0, slashIndex);
    const current = groups.get(key) ?? [];
    current.push(file);
    groups.set(key, current);
  }

  return [...groups.entries()]
    .toSorted(([left], [right]) => left.localeCompare(right))
    .map(([key, groupedFiles]) => ({
      key,
      title: key === "ROOT" ? ungroupedLabel : key,
      files: [...groupedFiles].toSorted((left, right) =>
        left.relative_path.localeCompare(right.relative_path),
      ),
    }));
}

export function UploadPreviewPanel({
  files,
  deletingFileIds,
  onDelete,
}: UploadPreviewPanelProps): React.JSX.Element {
  const { t } = useTranslation();
  const [lightboxSrc, setLightboxSrc] = useState<string | undefined>();
  const [panelExpanded, setPanelExpanded] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);

  const ungroupedLabel = t("uploadPreview.ungrouped");
  const groups = useMemo(() => groupFiles(files, ungroupedLabel), [files, ungroupedLabel]);

  if (files.length === 0) {
    return <div className="upload-preview-empty">{t("uploadPreview.noImages")}</div>;
  }

  return (
    <div className="upload-preview-panel">
      <button
        type="button"
        className="upload-preview-panel-header"
        onClick={() => {
          setPanelExpanded((prev) => !prev);
        }}
      >
        <span>{panelExpanded ? "▾" : "▸"}</span>
        <h4>{t("uploadPreview.title")}</h4>
        <span className="upload-preview-panel-count">
          {t("uploadPreview.photoCount", { count: files.length })}
        </span>
      </button>

      {panelExpanded && <div className="upload-preview-groups">
        {groups.map((group) => {
          const expanded = expandedKeys.includes(group.key);
          return (
            <section key={group.key} className="upload-preview-group">
              <button
                type="button"
                className="upload-preview-group-header"
                onClick={() => {
                  setExpandedKeys((prev) =>
                    expanded
                      ? prev.filter((key) => key !== group.key)
                      : [...prev, group.key],
                  );
                }}
              >
                <span>{expanded ? "▾" : "▸"}</span>
                <span>{group.title}</span>
                <span className="upload-preview-group-count">
                  {t("uploadPreview.groupCount", { count: group.files.length })}
                </span>
              </button>

              {expanded && (
                <div className="upload-preview-grid">
                  {group.files.map((file) => {
                    const deleting = deletingFileIds.includes(file.file_id);
                    return (
                      <div key={file.file_id} className="upload-preview-card">
                        <button
                          type="button"
                          className="upload-preview-thumb-btn"
                          onClick={() => {
                            setLightboxSrc(`/api/v1/uploads/${file.session_id}/files/${file.file_id}`);
                          }}
                        >
                          <img
                            src={`/api/v1/uploads/${file.session_id}/files/${file.file_id}`}
                            alt={file.filename}
                            className="upload-preview-thumb"
                          />
                        </button>
                        <div className="upload-preview-meta">
                          <span title={file.relative_path} className="upload-preview-name">
                            {file.filename}
                          </span>
                          <button
                            type="button"
                            className="upload-preview-delete"
                            disabled={deleting}
                            onClick={() => {
                              onDelete(file.file_id);
                            }}
                          >
                            {deleting ? t("uploadPreview.deleting") : t("common.delete")}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </div>}

      {lightboxSrc !== undefined && (
        <div
          className="image-lightbox"
          onClick={() => {
            setLightboxSrc(undefined);
          }}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Escape") setLightboxSrc(undefined);
          }}
        >
          <img src={lightboxSrc} alt={t("sourceImages.lightboxAlt")} />
        </div>
      )}
    </div>
  );
}
