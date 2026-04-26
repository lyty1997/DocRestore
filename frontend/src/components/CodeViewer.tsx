/**
 * AGE-50：代码模式视图。
 *
 * 三栏布局（复用 .preview-split CSS）：
 *   左：file 列表（点击切换；compile_failed 标红）
 *   中：当前 file 文本（<pre>）
 *   右：source_pages 对应的原图缩略图列表
 *
 * 数据来源：GET /tasks/{id}/files-index → FilesIndex；
 * 文件正文按需 fetch /tasks/{id}/files/{path}。
 */

import { useCallback, useEffect, useState } from "react";

import {
  getCodeFileContent,
  getFilesIndex,
  getSourceImageUrl,
} from "../api/client";
import type { FilesIndex, FilesIndexEntry } from "../api/schemas";
import { useTranslation } from "../i18n";

interface CodeViewerProps {
  readonly taskId: string;
  /** 任务级 source-images 列表，用于把 page_stem 反查回完整文件名 */
  readonly allSourceImages: readonly string[];
}

/** 把 "DSC06835.col0" 拆为 page_stem="DSC06835" */
function stemFromSourcePage(sourcePage: string): string {
  const dotIdx = sourcePage.indexOf(".");
  return dotIdx > 0 ? sourcePage.slice(0, dotIdx) : sourcePage;
}

/**
 * 给定 SourceFile 的 source_pages，从任务级 allSourceImages 里找出
 * 实际存在的图片（按 stem 前缀匹配，大小写敏感系统下兼容 JPG/jpg 后缀）。
 */
function resolveSourceImages(
  entry: FilesIndexEntry,
  allSourceImages: readonly string[],
): string[] {
  const stems = new Set(entry.source_pages.map((sp) => stemFromSourcePage(sp)));
  const out: string[] = [];
  const seen = new Set<string>();
  for (const img of allSourceImages) {
    const base = img.split("/").pop() ?? img;
    const dotIdx = base.lastIndexOf(".");
    const stem = dotIdx > 0 ? base.slice(0, dotIdx) : base;
    if (stems.has(stem) && !seen.has(img)) {
      seen.add(img);
      out.push(img);
    }
  }
  return out;
}

export function CodeViewer({
  taskId,
  allSourceImages,
}: CodeViewerProps): React.JSX.Element {
  const { t } = useTranslation();

  const [index, setIndex] = useState<FilesIndex | undefined>();
  const [indexError, setIndexError] = useState<string | undefined>();
  const [indexLoading, setIndexLoading] = useState(true);

  const [selectedPath, setSelectedPath] = useState<string | undefined>();
  const [content, setContent] = useState<string>("");
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | undefined>();

  const loadIndex = useCallback(async () => {
    setIndexLoading(true);
    setIndexError(undefined);
    try {
      const data = await getFilesIndex(taskId);
      setIndex(data);
      if (data.length > 0 && data[0]) {
        setSelectedPath(data[0].path);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setIndexError(msg);
    } finally {
      setIndexLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    void loadIndex();
  }, [loadIndex]);

  useEffect(() => {
    if (selectedPath === undefined) {
      setContent("");
      return;
    }
    let cancelled = false;
    setContentLoading(true);
    setContentError(undefined);
    void getCodeFileContent(taskId, selectedPath)
      .then((text) => {
        if (!cancelled) setContent(text);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const msg = error instanceof Error ? error.message : String(error);
        setContentError(msg);
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, selectedPath]);

  if (indexLoading) {
    return (
      <div className="code-viewer-loading">
        {t("codeViewer.loadingIndex")}
      </div>
    );
  }
  if (indexError !== undefined) {
    return (
      <div className="code-viewer-error">
        {t("codeViewer.indexError")}: {indexError}
      </div>
    );
  }
  if (index === undefined || index.length === 0) {
    return (
      <div className="code-viewer-empty">
        {t("codeViewer.empty")}
      </div>
    );
  }

  const selectedEntry = index.find((e) => e.path === selectedPath);
  const selectedImages =
    selectedEntry === undefined
      ? []
      : resolveSourceImages(selectedEntry, allSourceImages);

  return (
    <div className="code-viewer">
      <aside className="code-file-list">
        <h4>{t("codeViewer.filesTitle", { count: index.length })}</h4>
        <ul>
          {index.map((entry) => {
            const isSelected = entry.path === selectedPath;
            const isFailed = entry.compile_status === "failed";
            const isPassed = entry.compile_status === "passed";
            return (
              <li key={entry.path}>
                <button
                  type="button"
                  className={
                    "code-file-item" +
                    (isSelected ? " active" : "") +
                    (isFailed ? " compile-failed" : "") +
                    (isPassed ? " compile-passed" : "")
                  }
                  onClick={() => {
                    setSelectedPath(entry.path);
                  }}
                  title={
                    isFailed
                      ? (entry.compile_error ?? "compile failed")
                      : entry.path
                  }
                >
                  <span className="code-file-name">{entry.filename}</span>
                  <span className="code-file-meta">
                    {entry.line_count.toString()}{" "}
                    {t("codeViewer.lines")}
                    {entry.flags.length > 0
                      ? ` · ${entry.flags.length.toString()} ⚑`
                      : ""}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </aside>

      <main className="code-content">
        {selectedEntry !== undefined && (
          <div className="code-content-header">
            <code>{selectedEntry.path}</code>
            {selectedEntry.compile_status !== undefined &&
              selectedEntry.compile_status !== null && (
                <span
                  className={`compile-badge compile-${selectedEntry.compile_status}`}
                >
                  {t(`codeViewer.compile.${selectedEntry.compile_status}`)}
                </span>
              )}
            {selectedEntry.flags.length > 0 && (
              <details className="code-flags">
                <summary>
                  {t("codeViewer.flags", {
                    count: selectedEntry.flags.length,
                  })}
                </summary>
                <ul>
                  {selectedEntry.flags.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
        {contentLoading && (
          <div className="code-content-loading">
            {t("codeViewer.loadingFile")}
          </div>
        )}
        {contentError !== undefined && (
          <div className="code-content-error">
            {t("codeViewer.fileError")}: {contentError}
          </div>
        )}
        {!contentLoading && contentError === undefined && (
          <pre className="code-content-text">{content}</pre>
        )}
      </main>

      <aside className="code-source-images">
        <h4>{t("codeViewer.sourcePagesTitle")}</h4>
        {selectedEntry !== undefined && selectedEntry.source_pages.length > 0 && (
          <ul className="code-source-pages-list">
            {selectedEntry.source_pages.map((sp) => (
              <li key={sp} className="code-source-page-tag">
                {sp}
              </li>
            ))}
          </ul>
        )}
        <div className="code-source-images-list">
          {selectedImages.length === 0 && (
            <div className="code-source-images-empty">
              {t("codeViewer.noSourceImages")}
            </div>
          )}
          {selectedImages.map((name) => (
            <img
              key={name}
              src={getSourceImageUrl(taskId, name)}
              alt={name}
              title={name}
              className="code-source-image-item"
            />
          ))}
        </div>
      </aside>
    </div>
  );
}
