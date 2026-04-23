/**
 * 任务结果展示组件：多文档切换 + 源图片 + Markdown 编辑/预览 + 下载
 */

import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";

import {
  getDownloadUrl,
  listSourceImages,
  updateResultMarkdown,
} from "../api/client";
import type { TaskResultResponse } from "../api/schemas";
import { preprocessMarkdown } from "../features/task/markdown";
import { useScrollSync } from "../hooks/useScrollSync";
import { useTranslation } from "../i18n";
import { SourceImagePanel } from "./SourceImagePanel";

interface TaskResultProps {
  taskId: string;
  results: readonly TaskResultResponse[];
}

export function TaskResult({
  taskId,
  results: initialResults,
}: TaskResultProps): React.JSX.Element {
  const { t } = useTranslation();
  const [docResults, setDocResults] = useState<TaskResultResponse[]>([
    ...initialResults,
  ]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [allSourceImages, setAllSourceImages] = useState<string[]>([]);
  const [editMode, setEditMode] = useState(false);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | undefined>();
  const downloadUrl = getDownloadUrl(taskId);

  // 左右同步滚动：左侧 .source-images-list、右侧 .markdown-preview 两个
  // 可滚动容器，用 data-page="<filename>" 做对齐锚点。edit 模式下右侧是
  // textarea 没有 page 标记，禁用同步。
  const leftScrollRef = useRef<HTMLDivElement>(null);
  const rightScrollRef = useRef<HTMLDivElement>(null);

  const selectedDoc = docResults[selectedIdx];
  const docDir = selectedDoc?.doc_dir;
  const displayMarkdown = selectedDoc?.markdown ?? "";
  const rewritten = preprocessMarkdown(displayMarkdown, taskId, docDir);

  const filteredImages = (() => {
    if (docDir === undefined || docDir === "") return allSourceImages;
    const prefix = `${docDir}/`;
    return allSourceImages.filter((img) => img.startsWith(prefix));
  })();

  const dirty =
    editMode &&
    selectedDoc !== undefined &&
    editText !== selectedDoc.markdown;

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
      await updateResultMarkdown(taskId, selectedIdx, editText);
      setDocResults((prev) =>
        prev.map((doc, idx) =>
          idx === selectedIdx ? { ...doc, markdown: editText } : doc,
        ),
      );
      setEditMode(false);
    } catch {
      setSaveError(t("common.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    setDocResults([...initialResults]);
    setSelectedIdx((prev) => {
      if (initialResults.length === 0) return 0;
      return prev < initialResults.length ? prev : 0;
    });
  }, [initialResults]);

  useEffect(() => {
    let cancelled = false;
    listSourceImages(taskId)
      .then((res) => {
        if (!cancelled) setAllSourceImages(res.images);
      })
      .catch(() => {
        /* 源图片加载失败不阻断主流程 */
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  useScrollSync(leftScrollRef, rightScrollRef, {
    align: "center",
    enabled: !editMode,
  });

  return (
    <div className="task-result">
      <div className="result-header">
        <h2>{t("taskResult.title")}</h2>
        <div className="preview-actions">
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
          <a href={downloadUrl} download className="download-btn">
            {t("taskResult.downloadZip")}
          </a>
        </div>
      </div>

      {/* 多文档切换 tab */}
      {docResults.length > 1 && (
        <div className="doc-tabs">
          {docResults.map((doc, idx) => (
            <button
              key={doc.doc_dir ?? idx.toString()}
              type="button"
              className={`doc-tab ${idx === selectedIdx ? "active" : ""}`}
              onClick={() => {
                if (editMode) setEditMode(false);
                setSelectedIdx(idx);
              }}
            >
              {doc.doc_title !== undefined && doc.doc_title !== ""
                ? doc.doc_title
                : t("taskResult.docTab", { index: idx + 1 })}
            </button>
          ))}
        </div>
      )}

      <div className="preview-split">
        <SourceImagePanel
          ref={leftScrollRef}
          taskId={taskId}
          images={filteredImages}
        />
        {editMode ? (
          <div className="markdown-editor">
            <textarea
              value={editText}
              onChange={(e) => { setEditText(e.target.value); }}
              spellCheck={false}
            />
          </div>
        ) : (
          <div ref={rightScrollRef} className="markdown-preview">
            <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
              {rewritten}
            </Markdown>
          </div>
        )}
      </div>
    </div>
  );
}
