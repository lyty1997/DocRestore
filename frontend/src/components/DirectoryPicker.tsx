/**
 * 目录浏览器弹窗：导航服务器目录树，选择目标目录
 */

import { useCallback, useEffect, useState } from "react";

import { browseDirs } from "../api/client";
import type { DirEntry } from "../api/schemas";
import { useTranslation } from "../i18n";

interface DirectoryPickerProps {
  /** 初始路径（为空/undefined 时从 ~ 开始） */
  readonly initialPath?: string | undefined;
  /** 选择目录后回调 */
  readonly onSelect: (path: string) => void;
  /** 关闭弹窗 */
  readonly onClose: () => void;
}

export function DirectoryPicker({
  initialPath,
  onSelect,
  onClose,
}: DirectoryPickerProps): React.JSX.Element {
  const { t } = useTranslation();
  const [currentPath, setCurrentPath] = useState("");
  const [parentPath, setParentPath] = useState<string | undefined>();
  const [entries, setEntries] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>();
  /** 用户在当前目录下手动输入的新目录名 */
  const [newDirName, setNewDirName] = useState("");

  const navigate = useCallback(async (path?: string) => {
    setLoading(true);
    setError(undefined);
    try {
      const resp = await browseDirs(path);
      setCurrentPath(resp.path);
      setParentPath(resp.parent ?? undefined);
      setEntries(resp.entries);
    } catch (error_: unknown) {
      setError(error_ instanceof Error ? error_.message : t("dirPicker.accessError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void navigate(initialPath ?? undefined);
  }, [initialPath, navigate]);

  const handleSelect = (): void => {
    const trimmed = newDirName.trim();
    if (trimmed) {
      // 用户指定了新子目录名，拼接到当前路径
      onSelect(`${currentPath}/${trimmed}`);
    } else {
      onSelect(currentPath);
    }
  };

  return (
    <div className="dir-picker-overlay" onClick={onClose}>
      <div
        className="dir-picker-modal"
        onClick={(e) => {
          e.stopPropagation();
        }}
      >
        <div className="dir-picker-header">
          <h3>{t("dirPicker.title")}</h3>
          <button type="button" className="dir-picker-close" onClick={onClose}>
            &times;
          </button>
        </div>

        {/* 当前路径 */}
        <div className="dir-picker-path">
          <span className="dir-picker-path-label">{t("dirPicker.currentPath")}</span>
          <code>{currentPath}</code>
        </div>

        {/* 目录列表 */}
        <div className="dir-picker-list">
          {loading && <p className="dir-picker-loading">{t("common.loading")}</p>}

          {error !== undefined && (
            <p className="dir-picker-error">{error}</p>
          )}

          {!loading && error === undefined && (
            <>
              {/* 返回上级 */}
              {parentPath !== undefined && (
                <button
                  type="button"
                  className="dir-entry dir-entry-parent"
                  onClick={() => {
                    void navigate(parentPath);
                  }}
                >
                  {t("dirPicker.parentDir")}
                </button>
              )}

              {entries.length === 0 && (
                <p className="dir-picker-empty">{t("dirPicker.emptyDir")}</p>
              )}

              {entries.map((entry) => (
                <button
                  type="button"
                  key={entry.name}
                  className="dir-entry"
                  onClick={() => {
                    void navigate(`${currentPath}/${entry.name}`);
                  }}
                >
                  {entry.name}/
                </button>
              ))}
            </>
          )}
        </div>

        {/* 新建子目录名输入 */}
        <div className="dir-picker-new">
          <input
            type="text"
            value={newDirName}
            onChange={(e) => {
              setNewDirName(e.target.value);
            }}
            placeholder={t("dirPicker.newDirPlaceholder")}
          />
        </div>

        {/* 操作按钮 */}
        <div className="dir-picker-actions">
          <button
            type="button"
            className="dir-picker-btn-select"
            onClick={handleSelect}
          >
            {newDirName.trim()
              ? t("dirPicker.selectWithDir", { path: currentPath, dir: newDirName.trim() })
              : t("dirPicker.selectPath", { path: currentPath })}
          </button>
          <button
            type="button"
            className="dir-picker-btn-cancel"
            onClick={onClose}
          >
            {t("common.cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
