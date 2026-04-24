/**
 * 单任务详情视图：展示任务信息 + 文档预览/编辑 + 源图片 + 操作按钮
 *
 * 从 TaskHistory 中提取，作为右侧主内容区查看历史任务的入口。
 */

import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";

import {
  cancelTask,
  deleteTask,
  getDownloadUrl,
  getTask,
  getTaskResults,
  listSourceImages,
  resumeTask,
  retryTask,
  updateResultMarkdown,
} from "../api/client";
import type { TaskListItem, TaskResultResponse } from "../api/schemas";
import { preprocessMarkdown } from "../features/task/markdown";
import { useTaskProgress } from "../features/task/useTaskProgress";
import { useScrollSync } from "../hooks/useScrollSync";
import { useTranslation } from "../i18n";
import { ConfirmDialog } from "./ConfirmDialog";
import { SourceImagePanel } from "./SourceImagePanel";
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

  /* 文档结果 */
  const [docResults, setDocResults] = useState<TaskResultResponse[]>([]);
  const [selectedDocIdx, setSelectedDocIdx] = useState(0);
  const [allSourceImages, setAllSourceImages] = useState<string[]>([]);
  const [resultsLoading, setResultsLoading] = useState(false);

  /* 编辑 */
  const [editMode, setEditMode] = useState(false);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | undefined>();

  /* 确认弹窗 */
  const [confirm, setConfirm] = useState<ConfirmState | undefined>();

  /* 左右预览同步滚动：SourceImagePanel 的 .source-images-list 与右侧
     .markdown-preview 各挂一个 callback ref，把实际 DOM 元素塞到 state
     里。state 变化触发 useScrollSync 内 useEffect 重新跑 → listener 绑上。
     edit 模式或失败 tab 下右侧不是 markdown，禁用同步。 */
  const [leftScrollEl, setLeftScrollEl] = useState<HTMLDivElement>();
  const [rightScrollEl, setRightScrollEl] = useState<HTMLDivElement>();

  const selectedDoc = docResults[selectedDocIdx];
  /**
   * 选中的子文档是否失败。失败 tab 不允许编辑、不展示 markdown 预览，
   * 只展示 error 文本；成功 tab 行为与历史版本一致。
   * zod 层 default("") 保证 error 始终为 string，这里直接比较空串即可。
   */
  const selectedDocFailed =
    selectedDoc !== undefined && selectedDoc.error !== "";
  const failedDocs = docResults.filter((d) => d.error !== "");
  const completedDocCount = docResults.length - failedDocs.length;
  const dirty =
    editMode &&
    selectedDoc !== undefined &&
    editText !== selectedDoc.markdown;

  /** 根据选中文档过滤源图片 */
  const filteredImages = (() => {
    if (selectedDoc?.doc_dir === undefined || selectedDoc.doc_dir === "") {
      return allSourceImages;
    }
    const prefix = `${selectedDoc.doc_dir}/`;
    return allSourceImages.filter((img) => img.startsWith(prefix));
  })();

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

  /** 加载结果和源图片 */
  const fetchResults = useCallback(async () => {
    setResultsLoading(true);
    try {
      const [results, images] = await Promise.all([
        getTaskResults(taskId),
        listSourceImages(taskId),
      ]);
      setDocResults(results.results);
      setSelectedDocIdx(0);
      setAllSourceImages(images.images);
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

  useScrollSync(leftScrollEl, rightScrollEl, {
    // 左侧是堆叠的小缩略图、右侧是长 markdown，形状差异大；用 start 对齐
    // 更贴合用户直觉：看到哪张图"在顶部可见"，右侧 markdown 也滚到对应
    // 段落"在顶部"，而不是把图居中。
    align: "start",
    enabled: !editMode && !selectedDocFailed,
  });

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

  /* 编辑相关 */
  const enterEdit = (): void => {
    if (selectedDoc !== undefined) {
      setEditText(selectedDoc.markdown);
      setEditMode(true);
      setSaveError(undefined);
    }
  };

  const handleSave = async (): Promise<void> => {
    if (selectedDoc === undefined) return;
    setSaving(true);
    setSaveError(undefined);
    try {
      await updateResultMarkdown(taskId, selectedDocIdx, editText);
      setDocResults((prev) =>
        prev.map((doc, idx) =>
          idx === selectedDocIdx ? { ...doc, markdown: editText } : doc,
        ),
      );
      setEditMode(false);
    } catch {
      setSaveError(t("common.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

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

      {/* 文档预览 */}
      {docResults.length > 0 && selectedDoc !== undefined && (
        <div className="task-detail-preview">
          <div className="preview-header">
            <h3>{t("taskDetail.docPreview")}</h3>
            <div className="preview-actions">
              {!selectedDocFailed && (
                <>
                  <div className="edit-preview-toggle">
                    <button
                      type="button"
                      className={`toggle-btn ${editMode ? "" : "active"}`}
                      onClick={() => {
                        setEditMode(false);
                      }}
                    >
                      {t("common.preview")}
                    </button>
                    <button
                      type="button"
                      className={`toggle-btn ${editMode ? "active" : ""}`}
                      onClick={enterEdit}
                    >
                      {t("common.edit")}
                    </button>
                  </div>
                  {editMode && (
                    <button
                      type="button"
                      className="save-btn"
                      disabled={saving || !dirty}
                      onClick={() => {
                        void handleSave();
                      }}
                    >
                      {saving ? t("common.saving") : t("common.save")}
                    </button>
                  )}
                  {saveError !== undefined && (
                    <span className="save-error">{saveError}</span>
                  )}
                </>
              )}
            </div>
          </div>

          {/* 多文档进度汇总（仅多文档场景） */}
          {docResults.length > 1 && (
            <div className="doc-summary">
              {failedDocs.length > 0
                ? t("taskDetail.docSummaryPartial", {
                    done: completedDocCount,
                    total: docResults.length,
                    failed: failedDocs.length,
                  })
                : t("taskDetail.docSummaryAll", {
                    total: docResults.length,
                  })}
            </div>
          )}

          {/* 多文档切换；失败子文档带 ✗ 徽章 */}
          {docResults.length > 1 && (
            <div className="doc-tabs">
              {docResults.map((doc, idx) => {
                const isFailed = doc.error !== "";
                /* 优先展示 doc_title；缺失 → doc_dir；两者都空 → "文档 N" */
                let label: string;
                if (doc.doc_title !== undefined && doc.doc_title !== "") {
                  label = doc.doc_title;
                } else if (
                  doc.doc_dir !== undefined && doc.doc_dir !== ""
                ) {
                  label = doc.doc_dir;
                } else {
                  label = t("taskResult.docTab", { index: idx + 1 });
                }
                return (
                  <button
                    key={doc.doc_dir ?? idx.toString()}
                    type="button"
                    className={
                      "doc-tab "
                      + (idx === selectedDocIdx ? "active " : "")
                      + (isFailed ? "doc-tab--failed" : "doc-tab--ok")
                    }
                    onClick={() => {
                      if (editMode) setEditMode(false);
                      setSelectedDocIdx(idx);
                    }}
                    title={isFailed ? doc.error : ""}
                  >
                    <span className="doc-tab-badge" aria-hidden="true">
                      {isFailed ? "✗" : "✓"}
                    </span>
                    {label}
                  </button>
                );
              })}
            </div>
          )}

          <div className="preview-split">
            <SourceImagePanel
              ref={(el) => { setLeftScrollEl(el ?? undefined); }}
              taskId={taskId}
              images={filteredImages}
            />
            {selectedDocFailed && (
              <div className="doc-failed-panel">
                <h4>{t("taskDetail.docFailedTitle")}</h4>
                <pre className="doc-failed-message">{selectedDoc.error}</pre>
                <p className="doc-failed-hint">
                  {t("taskDetail.docFailedHint")}
                </p>
              </div>
            )}
            {!selectedDocFailed && editMode && (
              <div className="markdown-editor">
                <textarea
                  value={editText}
                  onChange={(e) => {
                    setEditText(e.target.value);
                  }}
                  spellCheck={false}
                />
              </div>
            )}
            {!selectedDocFailed && !editMode && (
              <div
                ref={(el) => { setRightScrollEl(el ?? undefined); }}
                className="markdown-preview"
              >
                <Markdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeRaw]}
                >
                  {preprocessMarkdown(
                    selectedDoc.markdown,
                    taskId,
                    selectedDoc.doc_dir,
                  )}
                </Markdown>
              </div>
            )}
          </div>
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
