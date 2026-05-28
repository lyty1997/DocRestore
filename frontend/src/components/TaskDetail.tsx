/**
 * 单任务详情视图：展示任务信息 + 文档预览/编辑 + 源图片 + 操作按钮
 *
 * 从 TaskHistory 中提取，作为右侧主内容区查看历史任务的入口。
 */

import { useCallback, useEffect, useState } from "react";

import {
  cancelTask,
  deleteTask,
  getDownloadUrl,
  getTask,
  getTaskResults,
  resumeTask,
  retryTask,
} from "../api/client";
import type { TaskListItem, TaskResultResponse } from "../api/schemas";
import { useTaskProgress } from "../features/task/useTaskProgress";
import { useTranslation } from "../i18n";
import { ConfirmDialog } from "./ConfirmDialog";
import { DocCodePreview } from "./DocCodePreview";
import { TaskProgress } from "./TaskProgress";

/** 格式化时间（locale 由 i18n 提供） */
function formatTime(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleString(locale);
  } catch {
    return iso;
  }
}

interface TaskDetailProps {
  /** 要查看的任务 ID */
  readonly taskId: string;
  /** 任务被删除后回调（回到新建模式） */
  readonly onDeleted: () => void;
  /** 侧边栏任务列表刷新回调 */
  readonly onTaskListRefresh: () => void;
  /**
   * 切换到另一个 task 的回调（resume/retry 返回的新 task_id 用它跳转）。
   * 父组件通常绑到 App.setSelectedTaskId。
   */
  readonly onSelectTask: (taskId: string) => void;
}

/** 确认弹窗状态 */
interface ConfirmState {
  action: "cancel" | "delete";
  title: string;
  message: string;
}

export function TaskDetail({
  taskId,
  onDeleted,
  onTaskListRefresh,
  onSelectTask,
}: TaskDetailProps): React.JSX.Element {
  const { t } = useTranslation();
  /* 任务元信息 */
  const [task, setTask] = useState<TaskListItem | undefined>();
  const [taskLoading, setTaskLoading] = useState(true);
  const [taskError, setTaskError] = useState<string | undefined>();

  /* 文档结果（DocCodePreview 内部维持 selectedIdx / source-images / 代码模式
     探测 / 编辑状态，TaskDetail 只持有 results 顶层数组以便 retry 后刷新）。 */
  const [docResults, setDocResults] = useState<TaskResultResponse[]>([]);
  const [resultsLoading, setResultsLoading] = useState(false);

  /* 确认弹窗 */
  const [confirm, setConfirm] = useState<ConfirmState | undefined>();

  /** 加载任务元信息 */
  const fetchTaskInfo = useCallback(async () => {
    setTaskLoading(true);
    setTaskError(undefined);
    try {
      const resp = await getTask(taskId);
      /* getTask 返回 TaskResponse，手动构造 TaskListItem 兼容字段 */
      setTask({
        task_id: resp.task_id,
        status: resp.status,
        image_dir: "",
        output_dir: "",
        error: resp.error ?? undefined,
        created_at: "",
        result_count: 0,
      });
    } catch {
      setTaskError(t("taskDetail.loadError"));
    } finally {
      setTaskLoading(false);
    }
  }, [taskId, t]);

  /** 加载结果（源图 / 代码模式探测在 DocCodePreview 内完成） */
  const fetchResults = useCallback(async () => {
    setResultsLoading(true);
    try {
      const results = await getTaskResults(taskId);
      setDocResults(results.results);
    } catch {
      /* 未完成的任务没有结果，静默处理 */
    } finally {
      setResultsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    void fetchTaskInfo();
    void fetchResults();
  }, [fetchTaskInfo, fetchResults]);

  /* 实时进度订阅：pending/processing 时建 WS，终态自动停 + 刷新任务信息/结果 */
  const taskStatus = task?.status ?? "unknown";
  const isLive = taskStatus === "pending" || taskStatus === "processing";
  const handleTerminal = useCallback(
    (_kind: "completed" | "failed"): void => {
      /* 收到终态 → 重新拉任务元信息 + 结果 + 刷新侧边栏（状态徽章要更新） */
      void fetchTaskInfo();
      void fetchResults();
      onTaskListRefresh();
    },
    [fetchTaskInfo, fetchResults, onTaskListRefresh],
  );
  const {
    progresses,
    wsState,
    pollingEnabled,
    llmUnavailable,
  } = useTaskProgress({
    taskId,
    enabled: isLive,
    onTerminal: handleTerminal,
  });

  /* 操作 */
  const handleConfirm = async (): Promise<void> => {
    if (confirm === undefined) return;
    try {
      if (confirm.action === "cancel") {
        await cancelTask(taskId);
        onTaskListRefresh();
        void fetchTaskInfo();
      } else {
        await deleteTask(taskId);
        onTaskListRefresh();
        onDeleted();
      }
    } catch {
      setTaskError(confirm.action === "cancel" ? t("taskDetail.cancelFailed") : t("taskDetail.deleteFailed"));
    } finally {
      setConfirm(undefined);
    }
  };

  const handleRetry = async (): Promise<void> => {
    try {
      const resp = await retryTask(taskId);
      onTaskListRefresh();
      /* 切到新建的 task，让详情页自动订阅进度 */
      onSelectTask(resp.task_id);
    } catch {
      setTaskError(t("taskDetail.retryFailed"));
    }
  };

  const handleResume = async (): Promise<void> => {
    try {
      const resp = await resumeTask(taskId);
      onTaskListRefresh();
      onSelectTask(resp.task_id);
    } catch {
      setTaskError(t("taskDetail.resumeFailed"));
    }
  };

  /* 加载中 */
  if (taskLoading) {
    return <div className="task-detail-loading">{t("taskDetail.loadingTask")}</div>;
  }

  if (taskError !== undefined) {
    return <div className="task-detail-error">{taskError}</div>;
  }

  const status = task?.status ?? "unknown";

  return (
    <div className="task-detail">
      {/* 确认弹窗 */}
      {confirm !== undefined && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          onConfirm={() => void handleConfirm()}
          onCancel={() => {
            setConfirm(undefined);
          }}
        />
      )}

      {/* 任务信息头 */}
      <div className="task-detail-header">
        <h2>{t("taskDetail.title")}</h2>
        <div className="task-detail-meta">
          <span className="task-detail-id">{t("taskDetail.idLabel", { taskId })}</span>
          <span className={`status-badge status-${status}`}>
            {t(`status.${status}`)}
          </span>
          {task?.created_at !== undefined && task.created_at !== "" && (
            <span className="task-detail-time">
              {formatTime(task.created_at, t("common.dateLocale"))}
            </span>
          )}
        </div>

        {/* 操作按钮 */}
        <div className="task-detail-actions">
          {(status === "pending" || status === "processing") && (
            <button
              type="button"
              className="action-btn btn-cancel"
              onClick={() => {
                setConfirm({
                  action: "cancel",
                  title: t("taskDetail.cancelTask"),
                  message: t("taskDetail.cancelConfirm", { taskId }),
                });
              }}
            >
              {t("taskDetail.cancelTask")}
            </button>
          )}

          {status === "completed" && (
            <>
              <a
                href={getDownloadUrl(taskId)}
                download
                className="download-btn"
              >
                {t("taskDetail.downloadZip")}
              </a>
              <button
                type="button"
                className="action-btn btn-delete"
                onClick={() => {
                  setConfirm({
                    action: "delete",
                    title: t("taskDetail.deleteTask"),
                    message: t("taskDetail.deleteConfirm", { taskId }),
                  });
                }}
              >
                {t("common.delete")}
              </button>
            </>
          )}

          {status === "failed" && (
            <>
              <button
                type="button"
                className="action-btn btn-resume"
                onClick={() => void handleResume()}
                title={t("taskDetail.resumeHint")}
              >
                {t("taskDetail.resumeTask")}
              </button>
              <button
                type="button"
                className="action-btn btn-retry"
                onClick={() => void handleRetry()}
                title={t("taskDetail.retryHint")}
              >
                {t("common.retry")}
              </button>
              <button
                type="button"
                className="action-btn btn-delete"
                onClick={() => {
                  setConfirm({
                    action: "delete",
                    title: t("taskDetail.deleteTask"),
                    message: t("taskDetail.deleteConfirm", { taskId }),
                  });
                }}
              >
                {t("common.delete")}
              </button>
            </>
          )}
        </div>
      </div>

      {/* 错误信息 */}
      {task?.error !== undefined && task.error !== "" && (
        <div className="task-detail-error-box">
          <strong>{t("taskDetail.errorLabel")}</strong>
          {task.error}
        </div>
      )}

      {/* 运行中任务的实时进度（含 process_tree 多子目录分轨） */}
      {isLive && (
        <section className="task-detail-progress">
          <TaskProgress
            taskId={taskId}
            progresses={progresses}
            wsState={wsState}
            pollingEnabled={pollingEnabled}
            llmUnavailable={llmUnavailable}
          />
        </section>
      )}

      {/* 结果加载中 */}
      {resultsLoading && (
        <div className="task-detail-loading">{t("taskDetail.loadingResults")}</div>
      )}

      {/* 文档/代码模式预览（共享组件） */}
      {docResults.length > 0 && (
        <div className="task-detail-preview">
          <div className="preview-header">
            <h3>{t("taskDetail.docPreview")}</h3>
          </div>
          <DocCodePreview
            taskId={taskId}
            results={docResults}
            onResultsChange={(next) => { setDocResults([...next]); }}
            failedDocStyle="panel"
          />
        </div>
      )}

      {/* 无结果提示（非加载中且无结果） */}
      {!resultsLoading &&
        docResults.length === 0 &&
        status !== "pending" &&
        status !== "processing" && (
          <div className="task-detail-empty">{t("taskDetail.noResults")}</div>
        )}
    </div>
  );
}
