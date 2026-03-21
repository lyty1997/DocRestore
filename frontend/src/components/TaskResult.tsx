/**
 * 任务结果展示组件：Markdown 预览 + 下载按钮
 */

import Markdown from "react-markdown";

import { getDownloadUrl } from "../api/client";
import { rewriteImageUrls } from "../features/task/markdown";

interface TaskResultProps {
  taskId: string;
  markdown: string;
}

export function TaskResult({ taskId, markdown }: TaskResultProps): React.JSX.Element {
  const rewritten = rewriteImageUrls(markdown, taskId);
  const downloadUrl = getDownloadUrl(taskId);

  return (
    <div className="task-result">
      <div className="result-header">
        <h2>处理结果</h2>
        <a
          href={downloadUrl}
          download
          className="download-btn"
        >
          下载结果（zip）
        </a>
      </div>
      <div className="markdown-preview">
        <Markdown>{rewritten}</Markdown>
      </div>
    </div>
  );
}
