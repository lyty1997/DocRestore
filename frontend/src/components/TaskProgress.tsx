/**
 * 任务进度展示组件
 */

import { useMemo } from "react";

import type { TaskProgress as TaskProgressData } from "../api/schemas";
import { useTranslation } from "../i18n";

interface TaskProgressProps {
  taskId: string | undefined;
  progress: TaskProgressData | undefined;
  wsState: string;
  pollingEnabled: boolean;
}

export function TaskProgress({
  taskId,
  progress,
  wsState,
  pollingEnabled,
}: TaskProgressProps): React.JSX.Element {
  const { t } = useTranslation();

  const stageLabels = useMemo<Record<string, string>>(
    () => ({
      init: t("taskProgress.stageInit"),
      ocr: t("taskProgress.stageOcr"),
      clean: t("taskProgress.stageClean"),
      merge: t("taskProgress.stageMerge"),
      refine: t("taskProgress.stageRefine"),
      render: t("taskProgress.stageRender"),
    }),
    [t],
  );

  const stageLabel = progress
    ? stageLabels[progress.stage] ?? progress.stage
    : t("taskProgress.waiting");
  const percent = progress?.percent ?? 0;

  return (
    <div className="task-progress">
      <div className="progress-header">
        <span className="task-id">
          {t("taskProgress.taskLabel", { taskId: taskId ?? "—" })}
        </span>
        <span className="connection-status">
          {wsState === "open" ? "WS" : (pollingEnabled ? t("taskProgress.polling") : wsState)}
        </span>
      </div>

      <div className="progress-bar-container">
        <div
          className="progress-bar"
          style={{ width: `${percent.toFixed(1)}%` }}
        />
      </div>

      <div className="progress-detail">
        <span className="stage">{stageLabel}</span>
        {progress ? (
          <span className="counts">
            {progress.current.toString()}/{progress.total.toString()} ({percent.toFixed(1)}%)
          </span>
        ) : undefined}
      </div>

      {progress?.message ? (
        <p className="progress-message">{progress.message}</p>
      ) : undefined}
    </div>
  );
}
