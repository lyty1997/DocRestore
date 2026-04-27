/**
 * 文档/代码模式预览面板（TaskResult / TaskDetail 共享）。
 *
 * 单源真相：已知任务 + 已知 results，本组件负责
 *   - 探测 `files-index.json` 是否存在 → 启用文档/代码视图切换
 *   - 文档视图：左源图 + 右 markdown / 编辑器；多文档 tab；失败 tab 错误面板
 *   - 代码视图：CodeViewer（左文件列表 + 中代码 + 右源图，含 lightbox）
 *
 * 外层只需提供任务级 header（标题/下载/删除 等），无需重复实现 viewMode、
 * 文档 tab、edit/save 状态机、源图同步滚动等公共行为。
 */

import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";

import {
  getFilesIndex,
  listSourceImages,
  updateResultMarkdown,
} from "../api/client";
import type { TaskResultResponse } from "../api/schemas";
import { preprocessMarkdown } from "../features/task/markdown";
import { useScrollSync } from "../hooks/useScrollSync";
import { useTranslation } from "../i18n";
import { CodeViewer } from "./CodeViewer";
import { MarkdownWysiwygEditor } from "./MarkdownWysiwygEditor";
import { SourceImagePanel } from "./SourceImagePanel";

interface DocCodePreviewProps {
  readonly taskId: string;
  readonly results: readonly TaskResultResponse[];
  /** 编辑保存成功时通知外层同步状态 */
  readonly onResultsChange: (next: readonly TaskResultResponse[]) => void;
  /** 失败子文档 UI 风格：'panel' = 显示错误面板（TaskDetail），
   *  'badge-only' = 仅 tab 上显示 ✗，不渲染错误面板（TaskResult） */
  readonly failedDocStyle?: "panel" | "badge-only";
  /** 是否在 header 渲染 caller 提供的额外按钮（例如下载） */
  readonly headerExtras?: React.ReactNode;
  /** 是否显示 header 区（含 view mode + edit toggle）。默认 true。 */
  readonly showHeader?: boolean;
}

/**
 * 计算选中子文档的源图列表（按 doc_dir 前缀过滤）。
 */
function filterImagesForDoc(
  allImages: readonly string[],
  docDir: string | undefined,
): readonly string[] {
  if (docDir === undefined || docDir === "") return allImages;
  const prefix = `${docDir}/`;
  return allImages.filter((img) => img.startsWith(prefix));
}

export function DocCodePreview({
  taskId,
  results,
  onResultsChange,
  failedDocStyle = "panel",
  headerExtras,
  showHeader = true,
}: DocCodePreviewProps): React.JSX.Element {
  const { t } = useTranslation();

  /* 文档选择 */
  const [selectedIdx, setSelectedIdx] = useState(0);

  /* 源图 */
  const [allSourceImages, setAllSourceImages] = useState<string[]>([]);

  /* 代码模式探测 + 视图切换 */
  const [codeAvailable, setCodeAvailable] = useState(false);
  const [viewMode, setViewMode] = useState<"doc" | "code">("doc");

  /* 编辑 */
  const [editMode, setEditMode] = useState(false);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | undefined>();

  /* 左右同步滚动：callback ref → state 触发 hook 重绑 listener */
  const [leftScrollEl, setLeftScrollEl] = useState<HTMLDivElement>();
  const [rightScrollEl, setRightScrollEl] = useState<HTMLDivElement>();

  const selectedDoc = results[selectedIdx];
  const selectedDocFailed =
    selectedDoc !== undefined && selectedDoc.error !== "";
  const failedDocs = results.filter((d) => d.error !== "");
  const completedDocCount = results.length - failedDocs.length;
  const dirty =
    editMode &&
    selectedDoc !== undefined &&
    editText !== selectedDoc.markdown;

  const filteredImages = filterImagesForDoc(
    allSourceImages,
    selectedDoc?.doc_dir,
  );

  /* results 长度变化时收敛 selectedIdx */
  useEffect(() => {
    setSelectedIdx((prev) =>
      results.length === 0 || prev >= results.length ? 0 : prev,
    );
  }, [results.length]);

  /* 源图列表 */
  useEffect(() => {
    let cancelled = false;
    listSourceImages(taskId)
      .then((res) => {
        if (!cancelled) setAllSourceImages(res.images);
      })
      .catch(() => {
        /* 源图加载失败不阻断主流程 */
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  /* 探测代码模式产物：files-index.json 存在 → 启用 toggle。
     非代码模式任务返回 404 / 空数组，setCodeAvailable=false 不显示 toggle。 */
  useEffect(() => {
    let cancelled = false;
    getFilesIndex(taskId)
      .then((idx) => {
        if (!cancelled) setCodeAvailable(idx.length > 0);
      })
      .catch(() => {
        if (!cancelled) setCodeAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  useScrollSync(leftScrollEl, rightScrollEl, {
    align: "start",
    enabled: !editMode && !selectedDocFailed && viewMode === "doc",
  });

  const enterEdit = useCallback((): void => {
    if (selectedDoc !== undefined) {
      setEditText(selectedDoc.markdown);
      setEditMode(true);
      setSaveError(undefined);
    }
  }, [selectedDoc]);

  const handleSave = useCallback(async (): Promise<void> => {
    if (selectedDoc === undefined) return;
    setSaving(true);
    setSaveError(undefined);
    try {
      await updateResultMarkdown(taskId, selectedIdx, editText);
      const next: TaskResultResponse[] = results.map((doc, idx) =>
        idx === selectedIdx ? { ...doc, markdown: editText } : doc,
      );
      onResultsChange(next);
      setEditMode(false);
    } catch {
      setSaveError(t("common.saveFailed"));
    } finally {
      setSaving(false);
    }
  }, [
    editText, onResultsChange, results, selectedDoc, selectedIdx, taskId, t,
  ]);

  const renderHeader = (): React.JSX.Element | undefined => {
    if (!showHeader) return undefined;
    const showEditToggle =
      viewMode === "doc" && !selectedDocFailed && selectedDoc !== undefined;
    return (
      <div className="preview-actions">
        {codeAvailable && (
          <div className="view-mode-toggle">
            <button
              type="button"
              className={`toggle-btn ${viewMode === "doc" ? "active" : ""}`}
              onClick={() => {
                if (editMode) setEditMode(false);
                setViewMode("doc");
              }}
            >
              {t("taskDetail.viewModeDoc")}
            </button>
            <button
              type="button"
              className={`toggle-btn ${viewMode === "code" ? "active" : ""}`}
              onClick={() => {
                if (editMode) setEditMode(false);
                setViewMode("code");
              }}
            >
              {t("taskDetail.viewModeCode")}
            </button>
          </div>
        )}
        {showEditToggle && (
          <>
            <div className="edit-preview-toggle">
              <button
                type="button"
                className={`toggle-btn ${editMode ? "" : "active"}`}
                onClick={() => { setEditMode(false); }}
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
                onClick={() => { void handleSave(); }}
              >
                {saving ? t("common.saving") : t("common.save")}
              </button>
            )}
            {saveError !== undefined && (
              <span className="save-error">{saveError}</span>
            )}
          </>
        )}
        {headerExtras}
      </div>
    );
  };

  const renderDocSummary = (): React.JSX.Element | undefined => {
    if (results.length <= 1) return undefined;
    return (
      <div className="doc-summary">
        {failedDocs.length > 0
          ? t("taskDetail.docSummaryPartial", {
              done: completedDocCount,
              total: results.length,
              failed: failedDocs.length,
            })
          : t("taskDetail.docSummaryAll", { total: results.length })}
      </div>
    );
  };

  const renderDocTabs = (): React.JSX.Element | undefined => {
    if (results.length <= 1) return undefined;
    return (
      <div className="doc-tabs">
        {results.map((doc, idx) => {
          const isFailed = doc.error !== "";
          let label: string;
          if (doc.doc_title !== undefined && doc.doc_title !== "") {
            label = doc.doc_title;
          } else if (doc.doc_dir !== undefined && doc.doc_dir !== "") {
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
                + (idx === selectedIdx ? "active " : "")
                + (isFailed ? "doc-tab--failed" : "doc-tab--ok")
              }
              onClick={() => {
                if (editMode) setEditMode(false);
                setSelectedIdx(idx);
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
    );
  };

  const renderBody = (): React.JSX.Element => {
    if (codeAvailable && viewMode === "code") {
      return (
        <CodeViewer
          taskId={taskId}
          allSourceImages={allSourceImages}
        />
      );
    }
    if (selectedDoc === undefined) {
      return (
        <div className="task-detail-empty">{t("taskDetail.noResults")}</div>
      );
    }
    return (
      <div className="preview-split">
        <SourceImagePanel
          ref={(el) => { setLeftScrollEl(el ?? undefined); }}
          taskId={taskId}
          images={filteredImages}
        />
        {selectedDocFailed && failedDocStyle === "panel" && (
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
            <MarkdownWysiwygEditor
              value={editText}
              onChange={setEditText}
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
    );
  };

  return (
    <>
      {renderHeader()}
      {renderDocSummary()}
      {renderDocTabs()}
      {renderBody()}
    </>
  );
}
