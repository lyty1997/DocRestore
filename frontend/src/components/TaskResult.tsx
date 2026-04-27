/**
 * 新建任务完成后的主视图：标题 + 下载 + DocCodePreview。
 *
 * 多文档切换、源图、文档/代码视图、编辑保存等公共行为全部委托给
 * `<DocCodePreview>`，此处仅负责任务级 header（标题 + 下载按钮）。
 */

import { useState } from "react";

import { getDownloadUrl } from "../api/client";
import type { TaskResultResponse } from "../api/schemas";
import { useTranslation } from "../i18n";
import { DocCodePreview } from "./DocCodePreview";

interface TaskResultProps {
  /** App.tsx 传 ``key={taskId}`` 保证切换任务时整体重挂载，组件内部
   *  无需同步外层 results 变化 → 用 useState 初始值一次性吃掉 props。 */
  taskId: string;
  results: readonly TaskResultResponse[];
}

export function TaskResult({
  taskId,
  results: initialResults,
}: TaskResultProps): React.JSX.Element {
  const { t } = useTranslation();
  const [docResults, setDocResults] = useState<TaskResultResponse[]>(
    () => [...initialResults],
  );
  const downloadUrl = getDownloadUrl(taskId);

  return (
    <div className="task-result">
      <div className="result-header">
        <h2>{t("taskResult.title")}</h2>
      </div>
      <DocCodePreview
        taskId={taskId}
        results={docResults}
        onResultsChange={(next) => { setDocResults([...next]); }}
        failedDocStyle="panel"
        headerExtras={
          <a href={downloadUrl} download className="download-btn">
            {t("taskResult.downloadZip")}
          </a>
        }
      />
    </div>
  );
}
