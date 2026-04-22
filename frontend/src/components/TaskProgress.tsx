/**
 * 任务进度展示组件：每个子目录同时展示 OCR + LLM 精修双轨进度。
 *
 * - `progresses[""]` 对应任务级/单目录主进度；其它 key 为 process_tree 并行的
 *   子目录相对路径
 * - 每个桶分 `ocr` / `llm` 两轨：流式 Pipeline 的 OCR Producer 与 Stream
 *   Processor 并发运行，两条进度都需要可见
 */

import { useMemo } from "react";

import type { TaskProgress as TaskProgressData } from "../api/schemas";
import type {
  ProgressBuckets,
  SubtaskProgress,
} from "../features/task/progressPhase";
import { useTranslation } from "../i18n";

interface TaskProgressProps {
  taskId: string | undefined;
  progresses: ProgressBuckets;
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

  const subtasks = useMemo(
    () =>
      Object.entries(progresses)
        .filter(([key]) => key !== "")
        .toSorted(([a], [b]) => a.localeCompare(b))
        .map(([key, bucket]) => ({ key, bucket })),
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

      {/* 单目录任务且主桶已有帧时才渲染主进度双轨。
          - 任务初始 / 并行多子目录：主桶恒为空（process_tree 下所有帧都带非空
            subtask），这里不渲染空壳占位条
          - 单目录任务：首帧到来后主桶非空，渲染一行 OCR + 一行 LLM */}
      {subtasks.length === 0 && progresses[""] !== undefined ? (
        <PhaseRows
          bucket={progresses[""]}
          stageLabelOf={stageLabelOf}
          waitingLabel={t("taskProgress.waiting")}
          ocrLabel={t("taskProgress.phaseOcr")}
          llmLabel={t("taskProgress.phaseLlm")}
        />
      ) : undefined}

      {/* 并行子目录：每个子目录独立容器，内含 OCR / LLM 两条进度 */}
      {subtasks.length > 0 ? (
        <div className="subtasks">
          <div className="subtasks-header">
            {t("taskProgress.subtasksLabel", {
              count: subtasks.length.toString(),
            })}
          </div>
          {subtasks.map(({ key, bucket }) => (
            <div key={key} className="subtask-group">
              <div className="subtask-label">{key}</div>
              <PhaseRows
                bucket={bucket}
                stageLabelOf={stageLabelOf}
                waitingLabel={t("taskProgress.waiting")}
                ocrLabel={t("taskProgress.phaseOcr")}
                llmLabel={t("taskProgress.phaseLlm")}
                indent
              />
            </div>
          ))}
        </div>
      ) : undefined}
    </div>
  );
}

interface PhaseRowsProps {
  bucket: SubtaskProgress | undefined;
  stageLabelOf: (p: TaskProgressData | undefined) => string;
  waitingLabel: string;
  ocrLabel: string;
  llmLabel: string;
  indent?: boolean;
}

function PhaseRows({
  bucket,
  stageLabelOf,
  waitingLabel,
  ocrLabel,
  llmLabel,
  indent,
}: PhaseRowsProps): React.JSX.Element {
  const ocr = bucket?.ocr;
  const llm = bucket?.llm;
  return (
    <>
      <ProgressRow
        progress={ocr}
        stageLabel={stageLabelOf(ocr)}
        waitingLabel={waitingLabel}
        phaseLabel={ocrLabel}
        phaseKind="ocr"
        indent={indent === true}
      />
      <ProgressRow
        progress={llm}
        stageLabel={stageLabelOf(llm)}
        waitingLabel={waitingLabel}
        phaseLabel={llmLabel}
        phaseKind="llm"
        indent={indent === true}
      />
    </>
  );
}

interface ProgressRowProps {
  progress: TaskProgressData | undefined;
  stageLabel: string;
  waitingLabel: string;
  phaseLabel: string;
  phaseKind: "ocr" | "llm";
  indent: boolean;
}

function ProgressRow({
  progress,
  stageLabel,
  waitingLabel,
  phaseLabel,
  phaseKind,
  indent,
}: ProgressRowProps): React.JSX.Element {
  const { t } = useTranslation();
  const percent = progress?.percent ?? 0;
  // 流式未知总数：后端特意把 total=0 作为"动态切段中，总数尚未确定"的信号。
  // 此时 percent 一直是 0，显示 "N/0 (0.0%)" 体验差；改用 indeterminate 动画
  // 条 + "第 N 段（流式）" 文本。
  const isStreaming =
    progress !== undefined && progress.total === 0 && progress.current > 0;
  const rowClass = [
    "progress-row",
    "phase-row",
    `phase-row-${phaseKind}`,
    indent ? "phase-row-indent" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const barClass = [
    "progress-bar",
    `progress-bar-${phaseKind}`,
    isStreaming ? "progress-bar-indeterminate" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const barStyle = isStreaming ? undefined : { width: `${percent.toFixed(1)}%` };
  return (
    <div className={rowClass}>
      <div className="phase-label">{phaseLabel}</div>
      <div className="progress-bar-container">
        <div className={barClass} style={barStyle} />
      </div>
      <div className="progress-detail">
        <span className="stage">{stageLabel}</span>
        {progress ? (
          <span className="counts">
            {isStreaming
              ? t("taskProgress.streamingCount", {
                  current: progress.current.toString(),
                })
              : `${progress.current.toString()}/${progress.total.toString()} (${percent.toFixed(1)}%)`}
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
