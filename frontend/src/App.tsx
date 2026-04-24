/**
 * DocRestore 前端主页面
 *
 * 左侧边栏（任务列表） + 右侧内容区（新建/详情）布局
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { cancelTask } from "./api/client";
import { BackToTopButton } from "./components/BackToTopButton";
import { Sidebar } from "./components/Sidebar";
import { useTranslation } from "./i18n";
import { useTheme } from "./hooks/useTheme";
import type { SidebarTaskListHandle } from "./components/SidebarTaskList";
import { TaskDetail } from "./components/TaskDetail";
import { TaskForm } from "./components/TaskForm";
import { TaskProgress } from "./components/TaskProgress";
import { TaskResult } from "./components/TaskResult";
import { TokenSettings } from "./components/TokenSettings";
import { useTaskRunner } from "./features/task/useTaskRunner";
import "./App.css";

function App(): React.JSX.Element {
  const [selectedTaskId, setSelectedTaskId] = useState<string | undefined>();
  const [sidebarWidth, setSidebarWidth] = useState(220);
  const [showTokenSettings, setShowTokenSettings] = useState(false);
  const { theme, toggleTheme } = useTheme();
  const { t } = useTranslation();

  const taskListRef = useRef<SidebarTaskListHandle>(null);

  const {
    taskId,
    status,
    progresses,
    llmUnavailable,
    allResults,
    error,
    wsState,
    pollingEnabled,
    startTask,
    reset,
  } = useTaskRunner();

  const isProcessing = status === "pending" || status === "processing";

  const handleWidthChange = useCallback((w: number) => {
    setSidebarWidth(w);
  }, []);

  /** 新建任务模式 */
  const handleNewTask = useCallback(() => {
    setSelectedTaskId(undefined);
  }, []);

  /** 选中历史任务 */
  const handleSelectTask = useCallback((tid: string) => {
    setSelectedTaskId(tid);
  }, []);

  /** 刷新侧边栏任务列表 */
  const refreshTaskList = useCallback(() => {
    taskListRef.current?.refresh();
  }, []);

  /** 侧边栏删除任务回调：若被删的是当前选中的任务，退回新建模式 */
  const handleTaskDeleted = useCallback((tid: string) => {
    setSelectedTaskId((current) => (current === tid ? undefined : current));
  }, []);

  /** 任务完成/失败后刷新侧边栏 */
  const isTerminal = status === "completed" || status === "failed";
  const prevTerminalRef = useRef(false);
  useEffect(() => {
    if (isTerminal && !prevTerminalRef.current) {
      prevTerminalRef.current = true;
      refreshTaskList();
    } else if (!isTerminal) {
      prevTerminalRef.current = false;
    }
  }, [isTerminal, refreshTaskList]);

  /** 是否处于新建任务模式 */
  const isCreateMode = selectedTaskId === undefined;

  return (
    <div
      className="app"
      style={{ "--sidebar-width": `${String(sidebarWidth)}px` } as React.CSSProperties}
    >
      <Sidebar
        selectedTaskId={selectedTaskId}
        onSelectTask={handleSelectTask}
        onNewTask={handleNewTask}
        onTaskDeleted={handleTaskDeleted}
        onWidthChange={handleWidthChange}
        onTokenSettings={() => { setShowTokenSettings(true); }}
        theme={theme}
        onToggleTheme={toggleTheme}
        taskListRef={taskListRef}
      />

      {/* 右侧主内容 */}
      <main className="main-content">
        {/* 新建任务模式 */}
        {isCreateMode && (
          <>
            <section className="section-form">
              <TaskForm onSubmit={startTask} disabled={isProcessing} />
            </section>

            {isProcessing && (
              <section className="section-progress">
                <TaskProgress
                  taskId={taskId}
                  progresses={progresses}
                  wsState={wsState}
                  pollingEnabled={pollingEnabled}
                  llmUnavailable={llmUnavailable}
                />
                {taskId !== undefined && (
                  <button
                    type="button"
                    className="cancel-btn"
                    onClick={() => {
                      void cancelTask(taskId).then(() => {
                        reset();
                        refreshTaskList();
                      });
                    }}
                  >
                    {t("taskDetail.cancelTask")}
                  </button>
                )}
              </section>
            )}

            {status === "failed" && (
              <section className="section-error">
                <div className="error-box">
                  <h2>{t("app.processingFailed")}</h2>
                  <p>{error ?? t("app.unknownError")}</p>
                  <button type="button" onClick={reset}>
                    {t("taskResult.resetBtn")}
                  </button>
                </div>
              </section>
            )}

            {status === "completed" && taskId !== undefined && (
              <section className="section-result">
                <TaskResult key={taskId} taskId={taskId} results={allResults} />
                <button
                  type="button"
                  className="reset-btn"
                  onClick={reset}
                >
                  {t("taskResult.processNew")}
                </button>
              </section>
            )}
          </>
        )}

        {/* 查看历史任务详情 */}
        {!isCreateMode && (
          <TaskDetail
            key={selectedTaskId}
            taskId={selectedTaskId}
            onDeleted={() => {
              setSelectedTaskId(undefined);
              refreshTaskList();
            }}
            onTaskListRefresh={refreshTaskList}
            onSelectTask={handleSelectTask}
          />
        )}
      </main>

      {/* Token 设置弹窗 */}
      {showTokenSettings && (
        <TokenSettings onClose={() => { setShowTokenSettings(false); }} />
      )}

      {/* 回到顶部悬浮按钮（常驻右下角） */}
      <BackToTopButton />
    </div>
  );
}

export default App;
