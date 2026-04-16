/**
 * 通用二次确认弹窗
 */

import { useTranslation } from "../i18n";

interface ConfirmDialogProps {
  readonly title: string;
  readonly message: string;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <div
      className="confirm-overlay"
      onClick={onCancel}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
      role="button"
      tabIndex={0}
    >
      <div
        className="confirm-dialog"
        onClick={(e) => {
          e.stopPropagation();
        }}
        onKeyDown={() => {
          /* 内部不需要处理 */
        }}
        role="dialog"
        tabIndex={-1}
      >
        <h3>{title}</h3>
        <p>{message}</p>
        <div className="confirm-actions">
          <button type="button" className="btn-cancel" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button type="button" className="btn-confirm" onClick={onConfirm}>
            {t("common.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
