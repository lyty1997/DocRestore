/**
 * 任务进度展示组件：支持多子目录并行分轨。
 *
 * - `progresses[""]` 对应任务级/单目录主进度
 * - 其它 key 对应 process_tree 并行的各子目录，按 key 排序依次展开
 */

import { useMemo } from "react";

import type { TaskProgress as TaskProgressData } from "../api/schemas";
import { useTranslation } from "../i18n";

interface TaskProgressProps {
  taskId: string | undefined;
  progresses: Record<string, TaskProgressData>;
  wsState: string;
  pollingEnabled: boolean;
}

export function TaskProgress({
  taskId,
  progresses,
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

  const mainProgress = progresses[""];
  const subtasks = useMemo(
    () =>
      Object.entries(progresses)
        .filter(([key]) => key !== "")
        .toSorted(([a], [b]) => a.localeCompare(b))
        .map(([key, p]) => ({ key, p })),
    [progresses],
  );

  const stageLabelOf = (p: TaskProgressData | undefined): string =>
    p ? (stageLabels[p.stage] ?? p.stage) : t("taskProgress.waiting");

  return (
    <div className="task-progress">
      <div className="progress-header">
        <span className="task-id">
          {t("taskProgress.taskLabel", { taskId: taskId ?? "—" })}
        </span>
        <span className="connection-status">
          {wsState === "open"
            ? "WS"
            : (pollingEnabled ? t("taskProgress.polling") : wsState)}
        </span>
      </div>

      {/* 主进度条：任务级（单目录/顶层聚合帧） */}
      {mainProgress !== undefined || subtasks.length === 0 ? (
        <ProgressRow
          progress={mainProgress}
          stageLabel={stageLabelOf(mainProgress)}
          waitingLabel={t("taskProgress.waiting")}
        />
      ) : undefined}

      {/* 并行子目录：直接展开，不折叠 */}
      {subtasks.length > 0 ? (
        <div className="subtasks">
          <div className="subtasks-header">
            {t("taskProgress.subtasksLabel", {
              count: subtasks.length.toString(),
            })}
          </div>
          {subtasks.map(({ key, p }) => (
            <ProgressRow
              key={key}
              progress={p}
              stageLabel={stageLabelOf(p)}
              subtaskLabel={key}
              waitingLabel={t("taskProgress.waiting")}
            />
          ))}
        </div>
      ) : undefined}
    </div>
  );
}

interface ProgressRowProps {
  progress: TaskProgressData | undefined;
  stageLabel: string;
  waitingLabel: string;
  subtaskLabel?: string;
}

function ProgressRow({
  progress,
  stageLabel,
  waitingLabel,
  subtaskLabel,
}: ProgressRowProps): React.JSX.Element {
  const percent = progress?.percent ?? 0;
  return (
    <div
      className={
        subtaskLabel === undefined ? "progress-row" : "progress-row subtask-row"
      }
    >
      {subtaskLabel === undefined ? undefined : (
        <div className="subtask-label">{subtaskLabel}</div>
      )}
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
            {progress.current.toString()}/{progress.total.toString()} (
            {percent.toFixed(1)}%)
          </span>
        ) : (
          <span className="counts">{waitingLabel}</span>
        )}
      </div>
      {progress?.message ? (
        <p className="progress-message">{progress.message}</p>
      ) : undefined}
    </div>
  );
}
