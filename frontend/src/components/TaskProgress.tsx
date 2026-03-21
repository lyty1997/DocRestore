/**
 * 任务进度展示组件
 */

import type { TaskProgress as TaskProgressData } from "../api/schemas";

/** 阶段中文映射 */
const STAGE_LABELS: Record<string, string> = {
  ocr: "OCR 识别",
  clean: "文本清洗",
  merge: "去重合并",
  refine: "LLM 精修",
  render: "渲染输出",
};

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
  const stageLabel = progress
    ? STAGE_LABELS[progress.stage] ?? progress.stage
    : "等待开始";
  const percent = progress?.percent ?? 0;

  return (
    <div className="task-progress">
      <div className="progress-header">
        <span className="task-id">
          任务：{taskId ?? "—"}
        </span>
        <span className="connection-status">
          {wsState === "open" ? "WS" : (pollingEnabled ? "轮询" : wsState)}
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
