/**
 * 回到顶部悬浮按钮
 *
 * 常驻于视口右下角，点击后平滑滚动到页面顶部。
 * 不随页面滚动隐藏（用户明确要求时刻常驻）。
 */

import { useCallback } from "react";

import { useTranslation } from "../i18n";

export function BackToTopButton(): React.JSX.Element {
  const { t } = useTranslation();

  const handleClick = useCallback(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const label = t("backToTop.label");

  return (
    <button
      type="button"
      className="back-to-top-btn"
      onClick={handleClick}
      aria-label={label}
      title={label}
    >
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <polyline points="18 15 12 9 6 15" />
      </svg>
    </button>
  );
}
