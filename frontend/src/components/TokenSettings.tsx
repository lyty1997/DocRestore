/**
 * API Token 配置弹窗
 *
 * 对应服务端环境变量 DOCRESTORE_API_TOKEN。
 * 设计参考 TaskForm 的 LLM Key localStorage 持久化模式。
 */

import { useState } from "react";

import { clearApiToken, loadApiToken, saveApiToken } from "../api/auth";
import type { TokenSource } from "../api/schemas";
import { useTranslation } from "../i18n";

interface TokenSettingsProps {
  readonly onClose: () => void;
  /** 服务是否要求 token（来自 /auth/info）；false=insecure 无需设置。 */
  readonly authRequired?: boolean | undefined;
  /** token 来源（来自 /auth/info），用于定制"如何获取"指引；缺省按 device_file。 */
  readonly tokenSource?: TokenSource | undefined;
}

/** 遮蔽 token 显示：保留前 4 位和后 4 位 */
function maskToken(token: string): string {
  if (token.length <= 8) {
    return "*".repeat(token.length);
  }
  return `${token.slice(0, 4)}${"*".repeat(Math.min(token.length - 8, 8))}${token.slice(-4)}`;
}

export function TokenSettings(
  { onClose, authRequired, tokenSource }: TokenSettingsProps,
): React.JSX.Element {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(loadApiToken);
  const [draft, setDraft] = useState("");
  const hasSaved = current !== "";

  // 默认按 device_file（后端默认就是自动生成的 device token）给指引。
  const effectiveSource: TokenSource = tokenSource ?? "device_file";
  const showInsecureNote = authRequired === false;
  // 未保存且需要鉴权时才展示"如何获取"，引导用户先拿到 token 再粘贴。
  const showHowTo = !showInsecureNote && !hasSaved;

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

        {showInsecureNote && (
          <p className="token-insecure-note">
            {t("tokenSettings.insecureNote")}
          </p>
        )}

        {showHowTo && (
          <div className="token-howto">
            <p className="token-howto-title">{t("tokenSettings.howToTitle")}</p>
            {/* env 是特例；device_file / unknown / 缺省都回退到 device 指引 */}
            {effectiveSource === "env" ? (
              <ol className="token-howto-steps">
                <li>
                  {t("tokenSettings.howToEnvStep1")}
                  <code>DOCRESTORE_API_TOKEN</code>
                  {t("tokenSettings.howToEnvStep1Suffix")}
                </li>
                <li>{t("tokenSettings.howToEnvStep2")}</li>
              </ol>
            ) : (
              <ol className="token-howto-steps">
                <li>{t("tokenSettings.howToDeviceStep1")}</li>
                <li>
                  {t("tokenSettings.howToDeviceStep2")}
                  <code className="token-howto-cmd">
                    cat ~/.config/docrestore/device_token
                  </code>
                </li>
                <li>{t("tokenSettings.howToDeviceStep3")}</li>
              </ol>
            )}
            {effectiveSource !== "env" && (
              <p className="token-howto-note">
                {t("tokenSettings.howToDeviceNote")}
              </p>
            )}
          </div>
        )}

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
