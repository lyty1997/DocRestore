/**
 * 统一来源选择器：Tab 切换本地上传 / 服务器浏览，最终输出 imageDir。
 *
 * - 本地 Tab：复用 FileUploader（session 上传 → 临时 image_dir）
 * - 服务器 Tab：内嵌文件浏览，支持选目录或多选文件
 *   · 选目录 → 直接返回该目录路径作为 imageDir
 *   · 选文件 → 调 /sources/server 创建符号链接目录，返回该目录作为 imageDir
 */

import { useCallback, useEffect, useState } from "react";

import { browseDirs, stageServerSources } from "../api/client";
import type { DirEntry } from "../api/schemas";
import { useTranslation } from "../i18n";
import { FileUploader } from "./FileUploader";

type SourceTab = "local" | "server";

interface SourcePickerProps {
  /** 选定后回调，传入可直接用于 create_task 的 image_dir */
  readonly onComplete: (imageDir: string) => void;
  /** 外部传入的禁用开关（如任务提交中） */
  readonly disabled: boolean;
}

/** 将字节数格式化为 KB（保留 1 位小数） */
function formatSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "";
  return (bytes / 1024).toFixed(1);
}

/** 图片数上限（与后端 _IMAGE_COUNT_CAP 保持一致） */
const IMAGE_COUNT_CAP = 9999;

export function SourcePicker({
  onComplete,
  disabled,
}: SourcePickerProps): React.JSX.Element {
  const { t } = useTranslation();
  const [tab, setTab] = useState<SourceTab>("local");

  /* 服务器 Tab 状态 */
  const [currentPath, setCurrentPath] = useState("");
  const [parentPath, setParentPath] = useState<string | undefined>();
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [browseError, setBrowseError] = useState<string | undefined>();
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [staging, setStaging] = useState(false);
  const [stageError, setStageError] = useState<string | undefined>();
  const [confirmedDir, setConfirmedDir] = useState<string | undefined>();
  const [pathInput, setPathInput] = useState("");

  const navigate = useCallback(
    async (path?: string): Promise<void> => {
      setLoading(true);
      setBrowseError(undefined);
      setSelectedFiles(new Set());
      try {
        const resp = await browseDirs(path, true);
        setCurrentPath(resp.path);
        setParentPath(resp.parent ?? undefined);
        setEntries(resp.entries);
      } catch (error_: unknown) {
        setBrowseError(
          error_ instanceof Error ? error_.message : t("sourcePicker.browseError"),
        );
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  /* 首次进入服务器 Tab 时才加载，避免 local 用户也发请求 */
  useEffect(() => {
    if (tab === "server" && currentPath === "" && confirmedDir === undefined) {
      void navigate();
    }
  }, [tab, currentPath, confirmedDir, navigate]);

  const toggleSelect = (name: string): void => {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const handleUseThisDir = (): void => {
    setConfirmedDir(currentPath);
    onComplete(currentPath);
  };

  const handleUseSelectedFiles = async (): Promise<void> => {
    if (selectedFiles.size === 0) return;
    const paths = [...selectedFiles].map(
      (name) => `${currentPath}/${name}`,
    );
    setStaging(true);
    setStageError(undefined);
    try {
      const resp = await stageServerSources(paths);
      setConfirmedDir(resp.image_dir);
      onComplete(resp.image_dir);
    } catch (error_: unknown) {
      setStageError(
        error_ instanceof Error ? error_.message : t("sourcePicker.stageError"),
      );
    } finally {
      setStaging(false);
    }
  };

  const handleJumpPath = (): void => {
    const trimmed = pathInput.trim();
    if (trimmed === "") return;
    void navigate(trimmed);
  };

  const handleReset = (): void => {
    setConfirmedDir(undefined);
    setSelectedFiles(new Set());
    setStageError(undefined);
  };

  /* 已确认态：显示已选结果 + 重置按钮 */
  if (confirmedDir !== undefined) {
    return (
      <div className="source-picker source-picker-confirmed">
        <p className="source-picker-confirm-text">
          {t("sourcePicker.confirmed", { path: confirmedDir })}
        </p>
        <button
          type="button"
          className="btn-source-reset"
          onClick={handleReset}
          disabled={disabled}
        >
          {t("sourcePicker.reset")}
        </button>
      </div>
    );
  }

  return (
    <div className="source-picker">
      {/* Tab 切换 */}
      <div className="source-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "local"}
          className={`source-tab-btn${tab === "local" ? " active" : ""}`}
          onClick={() => {
            setTab("local");
          }}
          disabled={disabled}
        >
          {t("sourcePicker.localTab")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "server"}
          className={`source-tab-btn${tab === "server" ? " active" : ""}`}
          onClick={() => {
            setTab("server");
          }}
          disabled={disabled}
        >
          {t("sourcePicker.serverTab")}
        </button>
      </div>

      {/* 本地 Tab：复用 FileUploader */}
      {tab === "local" && (
        <div className="source-tab-panel">
          <FileUploader
            onComplete={(dir) => {
              setConfirmedDir(dir);
              onComplete(dir);
            }}
            disabled={disabled}
          />
        </div>
      )}

      {/* 服务器 Tab：目录+文件浏览 */}
      {tab === "server" && (
        <div className="source-tab-panel">
          <div className="server-picker-path">
            <span className="server-picker-path-label">
              {t("sourcePicker.currentPath")}
            </span>
            <code>{currentPath || "..."}</code>
          </div>

          {/* 路径跳转输入框 */}
          <div className="server-picker-jump">
            <input
              type="text"
              value={pathInput}
              onChange={(e) => {
                setPathInput(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleJumpPath();
                }
              }}
              placeholder={t("sourcePicker.pathPlaceholder")}
              disabled={disabled || loading}
            />
            <button
              type="button"
              className="btn-source-jump"
              onClick={handleJumpPath}
              disabled={disabled || loading || pathInput.trim() === ""}
            >
              {t("sourcePicker.goPath")}
            </button>
          </div>

          <div className="server-picker-list">
            {loading && (
              <p className="server-picker-loading">{t("common.loading")}</p>
            )}
            {browseError !== undefined && (
              <p className="server-picker-error">{browseError}</p>
            )}

            {!loading && browseError === undefined && (
              <>
                {parentPath !== undefined && (
                  <button
                    type="button"
                    className="server-entry server-entry-parent"
                    onClick={() => {
                      void navigate(parentPath);
                    }}
                    disabled={disabled}
                  >
                    {t("sourcePicker.parentDir")}
                  </button>
                )}

                {entries.length === 0 && (
                  <p className="server-picker-empty">
                    {t("sourcePicker.emptyDir")}
                  </p>
                )}

                {entries.map((entry) =>
                  entry.is_dir ? (
                    <button
                      type="button"
                      key={`d/${entry.name}`}
                      className="server-entry server-entry-dir"
                      onClick={() => {
                        void navigate(`${currentPath}/${entry.name}`);
                      }}
                      disabled={disabled}
                    >
                      <span className="server-entry-name">
                        {`📁 ${entry.name}/`}
                      </span>
                      {entry.image_count !== null &&
                        entry.image_count !== undefined &&
                        entry.image_count > 0 && (
                          <span className="server-entry-count">
                            {t("sourcePicker.imageCount", {
                              count:
                                entry.image_count >= IMAGE_COUNT_CAP
                                  ? `${IMAGE_COUNT_CAP.toString()}+`
                                  : entry.image_count.toString(),
                            })}
                          </span>
                        )}
                    </button>
                  ) : (
                    <label
                      key={`f/${entry.name}`}
                      className={`server-entry server-entry-file${
                        selectedFiles.has(entry.name) ? " selected" : ""
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedFiles.has(entry.name)}
                        onChange={() => {
                          toggleSelect(entry.name);
                        }}
                        disabled={disabled}
                        aria-label={t("sourcePicker.fileCheckboxAria", {
                          name: entry.name,
                        })}
                      />
                      <span className="server-entry-name">
                        {`🖼 ${entry.name}`}
                      </span>
                      {entry.size_bytes !== null && entry.size_bytes !== undefined && (
                        <span className="server-entry-size">
                          {t("sourcePicker.sizeKB", {
                            size: formatSize(entry.size_bytes),
                          })}
                        </span>
                      )}
                    </label>
                  ),
                )}
              </>
            )}
          </div>

          {stageError !== undefined && (
            <p className="server-picker-error">{stageError}</p>
          )}

          <div className="server-picker-actions">
            <button
              type="button"
              className="btn-source-use-dir"
              onClick={handleUseThisDir}
              disabled={disabled || loading || currentPath === ""}
            >
              {t("sourcePicker.useThisDir")}
            </button>
            <button
              type="button"
              className="btn-source-use-files"
              onClick={() => {
                void handleUseSelectedFiles();
              }}
              disabled={disabled || staging || selectedFiles.size === 0}
            >
              {t("sourcePicker.useSelectedFiles", {
                count: selectedFiles.size.toString(),
              })}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
