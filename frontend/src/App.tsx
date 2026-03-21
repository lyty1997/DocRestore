/**
 * DocRestore 前端主页面：单页闭环
 *
 * 输入图片目录 → 创建任务 → 展示进度 → 预览结果 → 下载 zip
 */

import { TaskForm } from "./components/TaskForm";
import { TaskProgress } from "./components/TaskProgress";
import { TaskResult } from "./components/TaskResult";
import { useTaskRunner } from "./features/task/useTaskRunner";
import "./App.css";

function App(): React.JSX.Element {
  const {
    taskId,
    status,
    progress,
    resultMarkdown,
    error,
    wsState,
    pollingEnabled,
    startTask,
    reset,
  } = useTaskRunner();

  const isProcessing = status === "pending" || status === "processing";

  return (
    <div className="app">
      <header className="app-header">
        <h1>DocRestore</h1>
        <p className="subtitle">文档照片还原为 Markdown</p>
      </header>

      <main className="app-main">
        {/* 输入区 */}
        <section className="section-form">
          <TaskForm onSubmit={startTask} disabled={isProcessing} />
        </section>

        {/* 进度区 */}
        {isProcessing && (
          <section className="section-progress">
            <TaskProgress
              taskId={taskId}
              progress={progress}
              wsState={wsState}
              pollingEnabled={pollingEnabled}
            />
          </section>
        )}

        {/* 错误区 */}
        {status === "failed" && (
          <section className="section-error">
            <div className="error-box">
              <h2>处理失败</h2>
              <p>{error ?? "未知错误"}</p>
              <button type="button" onClick={reset}>
                重新开始
              </button>
            </div>
          </section>
        )}

        {/* 结果区 */}
        {status === "completed" && taskId && resultMarkdown && (
          <section className="section-result">
            <TaskResult taskId={taskId} markdown={resultMarkdown} />
            <button type="button" className="reset-btn" onClick={reset}>
              处理新文档
            </button>
          </section>
        )}
      </main>
    </div>
  );
}

export default App;
