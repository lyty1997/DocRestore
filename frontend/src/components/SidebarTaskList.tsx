/**
 * 侧边栏任务列表：可折叠，显示正在执行和历史任务及其状态
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useState,
} from "react";

import { listTasks } from "../api/client";
import type { TaskListItem } from "../api/schemas";
import { useTranslation } from "../i18n";

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

export interface SidebarTaskListHandle {
  /** 外部触发刷新（如新任务创建后） */
  refresh: () => void;
}

interface SidebarTaskListProps {
  /** 当前选中的任务 ID */
  readonly selectedTaskId: string | undefined;
  /** 选中任务回调 */
  readonly onSelect: (taskId: string) => void;
  /** 侧边栏是否折叠 */
  readonly collapsed: boolean;
}

export const SidebarTaskList = forwardRef<
  SidebarTaskListHandle,
  SidebarTaskListProps
>(function SidebarTaskList(
  { selectedTaskId, onSelect, collapsed },
  ref,
): React.JSX.Element {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(true);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);

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
        <div className="stl-list">
          {tasks.length === 0 && !loading && (
            <div className="stl-empty">{t("taskList.empty")}</div>
          )}

          {tasks.map((task) => (
            <button
              key={task.task_id}
              type="button"
              className={[
                "stl-item",
                selectedTaskId === task.task_id ? "stl-item--active" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => {
                onSelect(task.task_id);
              }}
              title={`${task.task_id}\n${task.image_dir}`}
            >
              <span
                className={`stl-dot ${statusModifier(task.status)}`}
              />
              <span className="stl-item-id">{shortId(task.task_id)}</span>
              <span
                className={`stl-item-status ${statusModifier(task.status)}`}
              >
                {t(`status.${task.status}`)}
              </span>
              <span className="stl-item-time">
                {shortTime(task.created_at)}
              </span>
            </button>
          ))}

          {loading && <div className="stl-loading">{t("common.loading")}</div>}

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
      )}
    </div>
  );
});
