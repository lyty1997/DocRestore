/**
 * 侧边栏任务列表：可折叠，显示正在执行和历史任务及其状态
 *
 * 支持：
 * - 单项 "×" 删除（悬停显示），调用 DELETE /tasks/{id}
 * - 头部 "清理已结束" 批量删除 completed + failed（单次 POST /tasks/cleanup）
 *
 * 删除的任务若恰好是父组件当前选中的 taskId，会通过 onDeleted(taskId) 通知父组件
 * 重置 selectedTaskId 到新建模式。
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useState,
} from "react";

import { cleanupTasks, deleteTask, listTasks } from "../api/client";
import type { TaskListItem } from "../api/schemas";
import { useTranslation } from "../i18n";
import { ConfirmDialog } from "./ConfirmDialog";

/** 状态对应 CSS 修饰符 */
function statusModifier(status: string): string {
  return `stl-status--${status}`;
}

/** 截断 task ID 用于显示 */
function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

/** 格式化时间为短格式 */
function shortTime(iso: string): string {
  try {
    const d = new Date(iso);
    const month = (d.getMonth() + 1).toString().padStart(2, "0");
    const day = d.getDate().toString().padStart(2, "0");
    const hour = d.getHours().toString().padStart(2, "0");
    const minute = d.getMinutes().toString().padStart(2, "0");
    return `${month}-${day} ${hour}:${minute}`;
  } catch {
    return iso;
  }
}

const PAGE_SIZE = 10;
const FINISHED_STATUSES = ["completed", "failed"] as const;

export interface SidebarTaskListHandle {
  /** 外部触发刷新（如新任务创建后） */
  refresh: () => void;
}

interface SidebarTaskListProps {
  /** 当前选中的任务 ID */
  readonly selectedTaskId: string | undefined;
  /** 选中任务回调 */
  readonly onSelect: (taskId: string) => void;
  /**
   * 某个任务被删除后的回调。参数是被删除的 task_id（批量删除时可能多次调用）。
   * 父组件可据此清理 selectedTaskId。
   */
  readonly onDeleted: (taskId: string) => void;
  /** 侧边栏是否折叠 */
  readonly collapsed: boolean;
}

/** 二次确认弹窗状态 */
type ConfirmState =
  | { readonly kind: "single"; readonly taskId: string }
  | { readonly kind: "bulk"; readonly count: number };

export const SidebarTaskList = forwardRef<
  SidebarTaskListHandle,
  SidebarTaskListProps
>(function SidebarTaskList(
  { selectedTaskId, onSelect, onDeleted, collapsed },
  ref,
): React.JSX.Element {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(true);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState | undefined>();
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | undefined>();

  /** 排序：processing/pending 在前，其余按创建时间降序 */
  function sortTasks(list: TaskListItem[]): TaskListItem[] {
    return list.toSorted((a, b) => {
      const aActive = a.status === "processing" || a.status === "pending";
      const bActive = b.status === "processing" || b.status === "pending";
      if (aActive && !bActive) return -1;
      if (!aActive && bActive) return 1;
      return b.created_at.localeCompare(a.created_at);
    });
  }

  const fetchTasks = useCallback(
    async (pageNum: number, append: boolean) => {
      setLoading(true);
      try {
        const resp = await listTasks({ page: pageNum, page_size: PAGE_SIZE });
        setTotal(resp.total);
        setTasks((prev) =>
          sortTasks(append ? [...prev, ...resp.tasks] : resp.tasks),
        );
      } catch {
        /* 静默失败，保留已有列表 */
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  /** 初次加载 */
  useEffect(() => {
    void fetchTasks(1, false);
  }, [fetchTasks]);

  /** 暴露 refresh 给父组件 */
  useImperativeHandle(
    ref,
    () => ({
      refresh() {
        setPage(1);
        void fetchTasks(1, false);
      },
    }),
    [fetchTasks],
  );

  const handleLoadMore = (): void => {
    const nextPage = page + 1;
    setPage(nextPage);
    void fetchTasks(nextPage, true);
  };

  const hasMore = tasks.length < total;

  /** 当前列表中可被"清理已结束"命中的数量（仅作按钮提示，真值以后端为准） */
  const finishedLocalCount = tasks.filter((task) =>
    (FINISHED_STATUSES as readonly string[]).includes(task.status),
  ).length;

  const confirmMessage = ((): string => {
    if (confirm === undefined) return "";
    if (confirm.kind === "single") {
      return t("taskList.deleteConfirmMessage", { id: shortId(confirm.taskId) });
    }
    return t("taskList.clearFinishedMessage", { count: confirm.count });
  })();

  const confirmTitle = ((): string => {
    if (confirm === undefined) return "";
    if (confirm.kind === "single") return t("taskList.deleteConfirmTitle");
    return t("taskList.clearFinishedTitle");
  })();

  /** 单个删除 */
  const performSingleDelete = async (taskId: string): Promise<void> => {
    setBusy(true);
    setErrorMsg(undefined);
    try {
      await deleteTask(taskId);
      /* 本地立刻摘除以给即时反馈；页码/总数也同步更新 */
      setTasks((prev) => prev.filter((task) => task.task_id !== taskId));
      setTotal((prev) => Math.max(0, prev - 1));
      onDeleted(taskId);
    } catch {
      setErrorMsg(t("taskList.deleteFailed"));
    } finally {
      setBusy(false);
    }
  };

  /** 批量清理 completed + failed */
  const performBulkCleanup = async (): Promise<void> => {
    setBusy(true);
    setErrorMsg(undefined);
    try {
      const resp = await cleanupTasks(FINISHED_STATUSES);
      /* 通知父组件，清理掉可能被选中的 taskId */
      for (const tid of resp.deleted_ids) {
        onDeleted(tid);
      }
      /* 刷新列表拿权威状态 */
      setPage(1);
      await fetchTasks(1, false);
      if (resp.failed > 0) {
        setErrorMsg(
          t("taskList.clearFinishedResult", {
            ok: resp.deleted,
            fail: resp.failed,
          }),
        );
      }
    } catch {
      setErrorMsg(t("taskList.deleteFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async (): Promise<void> => {
    if (confirm === undefined) return;
    const current = confirm;
    setConfirm(undefined);
    await (current.kind === "single"
      ? performSingleDelete(current.taskId)
      : performBulkCleanup());
  };

  /* 侧边栏折叠时不渲染列表 */
  if (collapsed) return <></>;

  return (
    <div className="sidebar-task-list">
      <button
        type="button"
        className="stl-header"
        onClick={() => {
          setExpanded((prev) => !prev);
        }}
      >
        <span className="stl-arrow">{expanded ? "▾" : "▸"}</span>
        <span className="stl-title">{t("taskList.title")}</span>
        <span className="stl-count">{total.toString()}</span>
      </button>

      {expanded && (
        <>
          {finishedLocalCount > 0 && (
            <div className="stl-actions">
              <button
                type="button"
                className="stl-clear-btn"
                onClick={() => {
                  setConfirm({ kind: "bulk", count: finishedLocalCount });
                }}
                disabled={busy}
                title={t("taskList.clearFinished")}
              >
                {t("taskList.clearFinished")}
              </button>
            </div>
          )}

          {errorMsg !== undefined && (
            <div className="stl-error" role="alert">
              {errorMsg}
            </div>
          )}

          <div className="stl-list">
            {tasks.length === 0 && !loading && (
              <div className="stl-empty">{t("taskList.empty")}</div>
            )}

            {tasks.map((task) => {
              const isActive = selectedTaskId === task.task_id;
              const isRunning =
                task.status === "pending" || task.status === "processing";
              const rowClass = [
                "stl-item-row",
                isActive ? "stl-item-row--active" : "",
              ]
                .filter(Boolean)
                .join(" ");
              const itemClass = [
                "stl-item",
                isActive ? "stl-item--active" : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <div key={task.task_id} className={rowClass}>
                  <button
                    type="button"
                    className={itemClass}
                    onClick={() => {
                      onSelect(task.task_id);
                    }}
                    title={`${task.task_id}\n${task.image_dir}`}
                  >
                    <span
                      className={`stl-dot ${statusModifier(task.status)}`}
                    />
                    <span className="stl-item-id">
                      {shortId(task.task_id)}
                    </span>
                    <span
                      className={`stl-item-status ${statusModifier(task.status)}`}
                    >
                      {t(`status.${task.status}`)}
                    </span>
                    <span className="stl-item-time">
                      {shortTime(task.created_at)}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="stl-item-delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirm({ kind: "single", taskId: task.task_id });
                    }}
                    disabled={busy || isRunning}
                    title={
                      isRunning
                        ? t("taskList.cannotDeleteRunning")
                        : t("taskList.deleteItem")
                    }
                    aria-label={t("taskList.deleteItem")}
                  >
                    {"×"}
                  </button>
                </div>
              );
            })}

            {loading && (
              <div className="stl-loading">{t("common.loading")}</div>
            )}

            {hasMore && !loading && (
              <button
                type="button"
                className="stl-load-more"
                onClick={handleLoadMore}
              >
                {t("taskList.loadMore")}
              </button>
            )}
          </div>
        </>
      )}

      {confirm !== undefined && (
        <ConfirmDialog
          title={confirmTitle}
          message={confirmMessage}
          onConfirm={() => {
            void handleConfirm();
          }}
          onCancel={() => {
            setConfirm(undefined);
          }}
        />
      )}
    </div>
  );
});
