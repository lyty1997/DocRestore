/**
 * API Token 配置弹窗
 *
 * 对应服务端环境变量 DOCRESTORE_API_TOKEN。
 * 设计参考 TaskForm 的 LLM Key localStorage 持久化模式。
 */

import { useState } from "react";

import { clearApiToken, loadApiToken, saveApiToken } from "../api/auth";
import { useTranslation } from "../i18n";

interface TokenSettingsProps {
  readonly onClose: () => void;
}

/** 遮蔽 token 显示：保留前 4 位和后 4 位 */
function maskToken(token: string): string {
  if (token.length <= 8) {
    return "*".repeat(token.length);
  }
  return `${token.slice(0, 4)}${"*".repeat(Math.min(token.length - 8, 8))}${token.slice(-4)}`;
}

export function TokenSettings({ onClose }: TokenSettingsProps): React.JSX.Element {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(loadApiToken);
  const [draft, setDraft] = useState("");
  const hasSaved = current !== "";

  const handleSave = (): void => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    saveApiToken(trimmed);
    setCurrent(trimmed);
    setDraft("");
  };

  const handleClear = (): void => {
    clearApiToken();
    setCurrent("");
  };

  return (
    <div
      className="modal-overlay"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
      role="button"
      tabIndex={0}
    >
      <div
        className="modal-content token-settings"
        onClick={(e) => { e.stopPropagation(); }}
        onKeyDown={(e) => { e.stopPropagation(); }}
        role="dialog"
        aria-label={t("tokenSettings.ariaLabel")}
      >
        <h2>{t("tokenSettings.title")}</h2>
        <p className="token-hint">
          {t("tokenSettings.hintPrefix")}<code>DOCRESTORE_API_TOKEN</code>
          {t("tokenSettings.hintSuffix")}
        </p>

        {hasSaved ? (
          <div className="token-saved">
            <code className="token-mask">{maskToken(current)}</code>
            <button type="button" className="token-clear-btn" onClick={handleClear}>
              {t("common.clear")}
            </button>
          </div>
        ) : (
          <div className="token-input-row">
            <input
              type="password"
              value={draft}
              autoFocus
              onChange={(e) => { setDraft(e.target.value); }}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSave();
              }}
              placeholder={t("tokenSettings.placeholder")}
              className="token-input"
            />
            <button
              type="button"
              className="token-save-btn"
              onClick={handleSave}
              disabled={!draft.trim()}
            >
              {t("common.save")}
            </button>
          </div>
        )}

        <button type="button" className="modal-close-btn" onClick={onClose}>
          {t("common.close")}
        </button>
      </div>
    </div>
  );
}
