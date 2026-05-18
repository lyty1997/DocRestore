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

import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  getCodeFileContent,
  getFilesIndex,
  updateCodeFileContent,
} from "../api/client";
import type {
  CodeDiagnosticItem,
  FilesIndex,
  FilesIndexEntry,
} from "../api/schemas";
import { tokenizeCodeLine } from "../features/task/codeSyntax";
import {
  type SourceImageListItem,
  imageNameToPageKey,
} from "../features/task/sourceImagePreview";
import { usePreviewScrollSync } from "../hooks/usePreviewScrollSync";
import { useTranslation } from "../i18n";
import { SourceImageList } from "./SourceImageList";

interface CodeViewerProps {
  readonly taskId: string;
  /** 任务级 source-images 列表，用于把 page_stem 反查回完整文件名 */
  readonly allSourceImages: readonly string[];
}

interface CodeSourceImage extends SourceImageListItem {
  readonly sourcePage: string;
}

interface CodePageAnchor {
  readonly pageKey: string;
  readonly sourcePage: string;
  readonly lineIndex: number;
}

/** 把 "DSC06835.col0" 拆为 page_stem="DSC06835" */
function stemFromSourcePage(sourcePage: string): string {
  const dotIdx = sourcePage.indexOf(".");
  return dotIdx > 0 ? sourcePage.slice(0, dotIdx) : sourcePage;
}

function basename(path: string): string {
  return imageNameToPageKey(path);
}

function stemFromImageName(imageName: string): string {
  const base = basename(imageName);
  const dotIdx = base.lastIndexOf(".");
  return dotIdx > 0 ? base.slice(0, dotIdx) : base;
}

/**
 * 给定 SourceFile 的 source_pages，从任务级 allSourceImages 里找出
 * 实际存在的图片（按 stem 前缀匹配，大小写敏感系统下兼容 JPG/jpg 后缀）。
 */
function resolveSourceImages(
  entry: FilesIndexEntry,
  allSourceImages: readonly string[],
): CodeSourceImage[] {
  const imagesByStem = new Map<string, string>();
  for (const img of allSourceImages) {
    const stem = stemFromImageName(img);
    if (!imagesByStem.has(stem)) {
      imagesByStem.set(stem, img);
    }
  }

  const out: CodeSourceImage[] = [];
  const seen = new Set<string>();
  for (const sourcePage of entry.source_pages) {
    const stem = stemFromSourcePage(sourcePage);
    const imageName = imagesByStem.get(stem);
    if (imageName !== undefined && !seen.has(imageName)) {
      seen.add(imageName);
      out.push({
        name: imageName,
        pageKey: basename(imageName),
        sourcePage,
      });
    }
  }
  return out;
}

function buildCodePageAnchors(
  entry: FilesIndexEntry,
  imageMatches: readonly CodeSourceImage[],
  content: string,
): CodePageAnchor[] {
  if (imageMatches.length === 0) return [];

  const lineCount = Math.max(1, content.split("\n").length);
  const pageKeyBySourcePage = new Map<string, CodeSourceImage>();
  for (const match of imageMatches) {
    pageKeyBySourcePage.set(match.sourcePage, match);
  }

  const firstLineNo = entry.line_no_range[0] ?? 1;
  const anchors: CodePageAnchor[] = [];
  const ranges = entry.source_page_ranges
    .filter((range) => pageKeyBySourcePage.has(range.page))
    .toSorted((a, b) => a.start_line - b.start_line);

  if (ranges.length > 0) {
    for (const range of ranges) {
      const match = pageKeyBySourcePage.get(range.page);
      if (match === undefined) continue;
      anchors.push({
        pageKey: match.pageKey,
        sourcePage: range.page,
        lineIndex: clampLineIndex(range.start_line - firstLineNo, lineCount),
      });
    }
  } else {
    for (const [idx, match] of imageMatches.entries()) {
      anchors.push({
        pageKey: match.pageKey,
        sourcePage: match.sourcePage,
        lineIndex: clampLineIndex(
          Math.floor((idx * lineCount) / imageMatches.length),
          lineCount,
        ),
      });
    }
  }

  const seen = new Set<string>();
  return anchors.filter((anchor) => {
    const dedupeKey = `${anchor.pageKey}:${anchor.lineIndex.toString()}`;
    if (seen.has(dedupeKey)) return false;
    seen.add(dedupeKey);
    return true;
  });
}

function clampLineIndex(value: number, lineCount: number): number {
  return Math.max(0, Math.min(value, lineCount - 1));
}

function splitEditorLines(content: string): string[] {
  return content === "" ? [""] : content.split("\n");
}

function firstDisplayLine(entry: FilesIndexEntry | undefined): number {
  const first = entry?.line_no_range[0];
  return first ?? 1;
}

function displayLineNumber(entry: FilesIndexEntry, lineIndex: number): number {
  return firstDisplayLine(entry) + lineIndex;
}

function isCompileFailingLine(
  entry: FilesIndexEntry,
  lineIndex: number,
): boolean {
  const failingLines = entry.compile_failing_lines ?? [];
  if (failingLines.length === 0) return false;
  const localLineNo = lineIndex + 1;
  const displayLineNo = displayLineNumber(entry, lineIndex);
  return failingLines.includes(localLineNo) || failingLines.includes(displayLineNo);
}

function diagnosticItemsForLine(
  entry: FilesIndexEntry,
  lineIndex: number,
): CodeDiagnosticItem[] {
  const localLineNo = lineIndex + 1;
  const displayLineNo = displayLineNumber(entry, lineIndex);
  const items = entry.diagnostic?.items.filter(
    (item) => item.line === localLineNo || item.line === displayLineNo,
  ) ?? [];
  if (items.length > 0) return items;
  if (!isCompileFailingLine(entry, lineIndex)) return [];
  return [{
    line: localLineNo,
    column: 0,
    severity: "error",
    category: "syntax",
    code: "legacy_compile_failure",
    message: entry.compile_error ?? "diagnostic failed",
    source: "",
  }];
}

function diagnosticClass(items: readonly CodeDiagnosticItem[]): string {
  if (items.length === 0) return "";
  if (items.some((item) => item.category === "syntax")) {
    return " has-syntax-diagnostic";
  }
  if (items.some((item) => item.category === "dependency")) {
    return " has-dependency-diagnostic";
  }
  return " has-semantic-diagnostic";
}

function diagnosticTitle(items: readonly CodeDiagnosticItem[]): string | undefined {
  if (items.length === 0) return undefined;
  return items
    .map((item) => {
      const prefix = item.category === "" ? "diagnostic" : item.category;
      return `${prefix}: ${item.message}`;
    })
    .join("\n");
}

function lineCountForContent(content: string): number {
  return content === "" ? 0 : content.split("\n").length;
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
  const [draftContent, setDraftContent] = useState<string>("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | undefined>();
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | undefined>();
  const [codeScrollEl, setCodeScrollEl] = useState<HTMLDivElement>();
  const [imageScrollEl, setImageScrollEl] = useState<HTMLDivElement>();
  const editGutterRef = useRef<HTMLDivElement>(null);

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
      setDraftContent("");
      setEditing(false);
      return;
    }
    let cancelled = false;
    setContentLoading(true);
    setContentError(undefined);
    setSaveError(undefined);
    void getCodeFileContent(taskId, selectedPath)
      .then((text) => {
        if (!cancelled) {
          setContent(text);
          setDraftContent(text);
          setEditing(false);
        }
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

  const selectedEntry = index?.find((e) => e.path === selectedPath);
  const selectedImages = useMemo(
    () =>
      selectedEntry === undefined
        ? []
        : resolveSourceImages(selectedEntry, allSourceImages),
    [allSourceImages, selectedEntry],
  );
  const codePageAnchors = useMemo(
    () =>
      selectedEntry === undefined
        ? []
        : buildCodePageAnchors(selectedEntry, selectedImages, content),
    [content, selectedEntry, selectedImages],
  );

  usePreviewScrollSync(
    codeScrollEl,
    imageScrollEl,
    !contentLoading &&
      !editing &&
      contentError === undefined &&
      codePageAnchors.length > 0 &&
      selectedImages.length > 0,
  );

  const anchorsByLine = new Map<number, CodePageAnchor[]>();
  for (const anchor of codePageAnchors) {
    const anchors = anchorsByLine.get(anchor.lineIndex) ?? [];
    anchors.push(anchor);
    anchorsByLine.set(anchor.lineIndex, anchors);
  }
  const contentLines = splitEditorLines(content);
  const draftLines = splitEditorLines(draftContent);
  const dirty = editing && draftContent !== content;

  const handleSaveCode = async (): Promise<void> => {
    if (selectedPath === undefined || selectedEntry === undefined) return;
    setSaving(true);
    setSaveError(undefined);
    try {
      await updateCodeFileContent(taskId, selectedPath, draftContent);
      setContent(draftContent);
      setEditing(false);
      setIndex((prev) =>
        prev?.map((entry) =>
          entry.path === selectedPath
            ? {
                ...entry,
                line_count: lineCountForContent(draftContent),
                line_no_range:
                  draftContent === ""
                    ? []
                    : [
                        firstDisplayLine(selectedEntry),
                        firstDisplayLine(selectedEntry) +
                          lineCountForContent(draftContent) -
                          1,
                      ],
              }
            : entry,
        ),
      );
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : String(error);
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  };

  const syncEditGutterScroll = (
    event: React.UIEvent<HTMLTextAreaElement>,
  ): void => {
    if (editGutterRef.current !== null) {
      editGutterRef.current.scrollTop = event.currentTarget.scrollTop;
    }
  };

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

  return (
    <div className="code-viewer">
      <aside className="code-file-list">
        <h4>{t("codeViewer.filesTitle", { count: index.length })}</h4>
        <ul>
          {index.map((entry) => {
            const isSelected = entry.path === selectedPath;
            const isDependencyOnly = entry.diagnostic?.status === "dependency_dirty";
            const isFailed = entry.compile_status === "failed" && !isDependencyOnly;
            const isPassed = entry.compile_status === "passed";
            return (
              <li key={entry.path}>
                <button
                  type="button"
                  className={
                    "code-file-item" +
                    (isSelected ? " active" : "") +
                    (isFailed ? " compile-failed" : "") +
                    (isPassed ? " compile-passed" : "") +
                    (isDependencyOnly ? " compile-dependency" : "")
                  }
                  onClick={() => {
                    setSelectedPath(entry.path);
                  }}
                  title={
                    isFailed || isDependencyOnly
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
            <div className="code-editor-actions">
              {editing ? (
                <>
                  <button
                    type="button"
                    className="code-editor-btn"
                    disabled={saving}
                    onClick={() => {
                      setDraftContent(content);
                      setEditing(false);
                      setSaveError(undefined);
                    }}
                  >
                    {t("common.cancel")}
                  </button>
                  <button
                    type="button"
                    className="code-editor-btn primary"
                    disabled={saving || !dirty}
                    onClick={() => { void handleSaveCode(); }}
                  >
                    {saving ? t("common.saving") : t("common.save")}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="code-editor-btn"
                  onClick={() => {
                    setDraftContent(content);
                    setEditing(true);
                    setSaveError(undefined);
                  }}
                >
                  {t("common.edit")}
                </button>
              )}
            </div>
          </div>
        )}
        {saveError !== undefined && (
          <div className="code-save-error">
            {t("codeViewer.saveError")}: {saveError}
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
          <>
            {editing && selectedEntry !== undefined ? (
              <div className="code-editor-edit-wrap">
                <div
                  ref={editGutterRef}
                  className="code-editor-edit-gutter"
                  aria-hidden="true"
                >
                  {draftLines.map((_, lineIndex) => {
                      const lineItems = diagnosticItemsForLine(
                        selectedEntry,
                        lineIndex,
                      );
                      return (
                        <div
                          key={lineIndex}
                          className={
                            "code-line-number" +
                            diagnosticClass(lineItems)
                          }
                          title={diagnosticTitle(lineItems)}
                        >
                          {displayLineNumber(selectedEntry, lineIndex)}
                        </div>
                      );
                    })}
                </div>
                <textarea
                  className="code-editor-textarea"
                  value={draftContent}
                  spellCheck={false}
                  aria-label={t("codeViewer.editAreaLabel")}
                  onChange={(event) => {
                    setDraftContent(event.currentTarget.value);
                  }}
                  onScroll={syncEditGutterScroll}
                />
              </div>
            ) : (
              <div
                ref={(el) => { setCodeScrollEl(el ?? undefined); }}
                className="code-content-text"
              >
                {selectedEntry !== undefined &&
                  contentLines.map((line, lineIndex) => {
                    const lineItems = diagnosticItemsForLine(
                      selectedEntry,
                      lineIndex,
                    );
                    const lineDiagnosticClass = diagnosticClass(lineItems);
                    const lineTitle = diagnosticTitle(lineItems);
                    return (
                      <Fragment key={lineIndex}>
                        {(anchorsByLine.get(lineIndex) ?? []).map((anchor) => (
                          <span
                            key={anchor.sourcePage}
                            data-page={anchor.pageKey}
                            className="code-page-anchor"
                          />
                        ))}
                        <div
                          className={
                            "code-line" + lineDiagnosticClass
                          }
                          data-line={displayLineNumber(
                            selectedEntry,
                            lineIndex,
                          )}
                          title={lineTitle}
                        >
                          <span className="code-line-number">
                            {displayLineNumber(selectedEntry, lineIndex)}
                          </span>
                          <code
                            className={
                              "code-line-code" + lineDiagnosticClass
                            }
                          >
                            {tokenizeCodeLine(
                              line,
                              selectedEntry.language,
                              selectedEntry.path,
                            ).map((token, tokenIndex) => (
                              <span
                                key={`${lineIndex.toString()}-${tokenIndex.toString()}`}
                                className={`code-token code-token-${token.kind}`}
                              >
                                {token.text}
                              </span>
                            ))}
                          </code>
                        </div>
                      </Fragment>
                    );
                  })}
              </div>
            )}
          </>
        )}
      </main>

      <aside className="code-source-images">
        <h4>{t("codeViewer.sourcePagesTitle")}</h4>
        {selectedEntry !== undefined && selectedEntry.source_pages.length > 0 && (
          <details className="code-source-pages-details">
            <summary>
              {t("codeViewer.sourcePagesCount", {
                count: selectedEntry.source_pages.length,
              })}
            </summary>
            <ul className="code-source-pages-list">
              {selectedEntry.source_pages.map((sp) => (
                <li key={sp} className="code-source-page-tag">
                  {sp}
                </li>
              ))}
            </ul>
          </details>
        )}
        <SourceImageList
          ref={(el) => { setImageScrollEl(el ?? undefined); }}
          taskId={taskId}
          images={selectedImages}
          listClassName="code-source-images-list"
          imageClassName="code-source-image-item"
          empty={
            <div className="code-source-images-empty">
              {t("codeViewer.noSourceImages")}
            </div>
          }
        />
      </aside>
    </div>
  );
}
