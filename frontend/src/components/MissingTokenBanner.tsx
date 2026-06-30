/**
 * 顶部提示横幅：服务要求 API Token 但本地未配置时，提醒用户去「Token 设置」。
 *
 * 由 App 在 useAuthStatus().needsToken 为真时渲染（insecure 模式不渲染，避免误报）。
 */

import { useTranslation } from "../i18n";

interface MissingTokenBannerProps {
  /** 点击「去设置」打开 TokenSettings 弹窗。 */
  readonly onOpenSettings: () => void;
}

export function MissingTokenBanner(
  { onOpenSettings }: MissingTokenBannerProps,
): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="missing-token-banner" role="alert">
      <span className="missing-token-banner-icon" aria-hidden="true">
        ⚠️
      </span>
      <span className="missing-token-banner-msg">
        {t("tokenBanner.message")}
      </span>
      <button
        type="button"
        className="missing-token-banner-btn"
        onClick={onOpenSettings}
      >
        {t("tokenBanner.action")}
      </button>
    </div>
  );
}
