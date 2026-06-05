/**
 * 代码模式审查视图。
 *
 * 三栏布局（复用 .preview-split CSS）：
 *   左：源文件列表（按 diagnostic / compile 兼容字段标注风险）
 *   中：当前源文件文本、编辑态 textarea、实时诊断列表
 *   右：source_pages 对应的原图缩略图列表
 *
 * 数据来源：GET /tasks/{id}/files-index → FilesIndex；
 * 文件正文按需 fetch /tasks/{id}/files/{path}；编辑态草稿通过
 * POST /tasks/{id}/code-diagnostics 做只读实时诊断。
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  diagnoseCodeFileContent,
  getCodeFileContent,
  getFilesIndex,
  updateCodeFileContent,
} from "../api/client";
import type {
  CodeDiagnostic,
  CodeDiagnosticItem,
  FilesIndex,
  FilesIndexEntry,
} from "../api/schemas";
import { tokenizeCodeLine } from "../features/task/codeSyntax";
import { computeLineWindow } from "../features/task/lineWindow";
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

interface VisibleDiagnosticItem {
  readonly item: CodeDiagnosticItem;
  readonly lineIndex: number;
  readonly lineNumber: number;
  readonly key: string;
}

/** 把 "page06835.col0" 拆为 page_stem="page06835" */
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
  diagnostic: CodeDiagnostic | undefined = entry.diagnostic,
): CodeDiagnosticItem[] {
  const localLineNo = lineIndex + 1;
  const displayLineNo = displayLineNumber(entry, lineIndex);
  const items = diagnostic?.items.filter(
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

function diagnosticStorageKey(taskId: string, path: string): string {
  return `docrestore:accepted-diagnostics:${taskId}:${path}`;
}

function diagnosticKey(
  taskId: string,
  path: string,
  item: CodeDiagnosticItem,
  lineText: string,
): string {
  return [
    taskId,
    path,
    item.line.toString(),
    item.column.toString(),
    item.category,
    item.code,
    item.message,
    item.source,
    lineText.trim(),
  ].join("\u001F");
}

function readAcceptedDiagnosticKeys(taskId: string, path: string): Set<string> {
  try {
    const raw = globalThis.localStorage.getItem(
      diagnosticStorageKey(taskId, path),
    );
    if (raw === null) return new Set<string>();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set<string>();
    return new Set(parsed.filter((item): item is string => typeof item === "string"));
  } catch {
    return new Set<string>();
  }
}

function writeAcceptedDiagnosticKeys(
  taskId: string,
  path: string,
  keys: ReadonlySet<string>,
): void {
  globalThis.localStorage.setItem(
    diagnosticStorageKey(taskId, path),
    JSON.stringify([...keys]),
  );
}

function visibleDiagnosticsForContent(
  entry: FilesIndexEntry,
  lines: readonly string[],
  taskId: string,
  path: string,
  acceptedKeys: ReadonlySet<string>,
  diagnostic: CodeDiagnostic | undefined = entry.diagnostic,
): VisibleDiagnosticItem[] {
  const visible: VisibleDiagnosticItem[] = [];
  for (const [lineIndex, lineText] of lines.entries()) {
    const lineItems = diagnosticItemsForLine(entry, lineIndex, diagnostic);
    for (const item of lineItems) {
      const key = diagnosticKey(taskId, path, item, lineText);
      if (acceptedKeys.has(key)) continue;
      visible.push({
        item,
        lineIndex,
        lineNumber: displayLineNumber(entry, lineIndex),
        key,
      });
    }
  }
  return visible;
}

function filterAcceptedDiagnostics(
  items: readonly CodeDiagnosticItem[],
  lineText: string,
  taskId: string,
  path: string,
  acceptedKeys: ReadonlySet<string>,
): CodeDiagnosticItem[] {
  return items.filter(
    (item) => !acceptedKeys.has(diagnosticKey(taskId, path, item, lineText)),
  );
}

/** 行级虚拟化：视口上下各额外渲染的缓冲行数，防快速滚动露白 */
const CODE_OVERSCAN = 8;
/** 行高实测前的默认估值（font-size 0.85rem × line-height 1.55 ≈ 21px） */
const DEFAULT_CODE_ROW_HEIGHT = 21;

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
  const [liveDiagnostic, setLiveDiagnostic] = useState<
    CodeDiagnostic | undefined
  >();
  const [liveDiagnosticLoading, setLiveDiagnosticLoading] = useState(false);
  const [liveDiagnosticError, setLiveDiagnosticError] = useState<
    string | undefined
  >();
  const [acceptedDiagnosticKeys, setAcceptedDiagnosticKeys] = useState<
    Set<string>
  >(new Set<string>());
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | undefined>();
  const [codeScrollEl, setCodeScrollEl] = useState<HTMLDivElement>();
  const [imageScrollEl, setImageScrollEl] = useState<HTMLDivElement>();
  const editGutterRef = useRef<HTMLDivElement>(null);
  // D：行级虚拟化的滚动位置 / 视口高度 / 实测行高。
  const [codeScrollTop, setCodeScrollTop] = useState(0);
  const [codeViewportH, setCodeViewportH] = useState(800);
  const [codeRowH, setCodeRowH] = useState(DEFAULT_CODE_ROW_HEIGHT);

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
      setLiveDiagnostic(undefined);
      setLiveDiagnosticError(undefined);
      setAcceptedDiagnosticKeys(new Set<string>());
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
          setLiveDiagnostic(undefined);
          setLiveDiagnosticError(undefined);
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

  useEffect(() => {
    if (selectedPath === undefined) {
      setAcceptedDiagnosticKeys(new Set<string>());
      return;
    }
    setAcceptedDiagnosticKeys(readAcceptedDiagnosticKeys(taskId, selectedPath));
  }, [selectedPath, taskId]);

  const selectedEntry = index?.find((e) => e.path === selectedPath);

  useEffect(() => {
    if (!editing || selectedPath === undefined || selectedEntry === undefined) {
      setLiveDiagnostic(undefined);
      setLiveDiagnosticError(undefined);
      setLiveDiagnosticLoading(false);
      return;
    }

    let cancelled = false;
    setLiveDiagnosticLoading(true);
    setLiveDiagnosticError(undefined);
    const timeoutId = globalThis.setTimeout(() => {
      void diagnoseCodeFileContent(taskId, selectedPath, draftContent)
        .then((diagnostic) => {
          if (!cancelled) setLiveDiagnostic(diagnostic);
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          const msg = error instanceof Error ? error.message : String(error);
          setLiveDiagnosticError(msg);
        })
        .finally(() => {
          if (!cancelled) setLiveDiagnosticLoading(false);
        });
    }, 350);

    return () => {
      cancelled = true;
      globalThis.clearTimeout(timeoutId);
    };
  }, [draftContent, editing, selectedEntry, selectedPath, taskId]);

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

  // A：整文件一次性分词后缓存。键取 content + 语言 + 路径（均为基本值，
  // selectedEntry 每渲染换引用但字段值稳定），避免无关重渲染时重复切词。
  const tokenizedLines = useMemo(() => {
    const language = selectedEntry?.language;
    const path = selectedEntry?.path ?? "";
    return splitEditorLines(content).map((line) =>
      tokenizeCodeLine(line, language, path),
    );
  }, [content, selectedEntry?.language, selectedEntry?.path]);

  usePreviewScrollSync(
    codeScrollEl,
    imageScrollEl,
    !contentLoading &&
      !editing &&
      contentError === undefined &&
      codePageAnchors.length > 0 &&
      selectedImages.length > 0,
  );

  // D：跟踪 code 容器滚动位置与视口高度（rAF 节流），驱动可视窗口。
  useEffect(() => {
    const el = codeScrollEl;
    if (el === undefined) return;
    setCodeViewportH(el.clientHeight);
    setCodeScrollTop(el.scrollTop);
    let rafId: number | undefined;
    const onScroll = (): void => {
      if (rafId !== undefined) return;
      rafId = globalThis.requestAnimationFrame(() => {
        rafId = undefined;
        setCodeScrollTop(el.scrollTop);
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    const observer = new globalThis.ResizeObserver(() => {
      setCodeViewportH(el.clientHeight);
    });
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      observer.disconnect();
      if (rafId !== undefined) globalThis.cancelAnimationFrame(rafId);
    };
  }, [codeScrollEl]);

  // 实测行高纠偏：源码行单行不换行、行高均匀，取首个 .code-line 真实高度。
  useEffect(() => {
    const el = codeScrollEl;
    if (el === undefined) return;
    const sample = el.querySelector<HTMLElement>(".code-line");
    if (sample === null) return;
    const measured = sample.getBoundingClientRect().height;
    if (measured > 0) {
      setCodeRowH((prev) => (Math.abs(prev - measured) > 0.5 ? measured : prev));
    }
  }, [codeScrollEl, content, codeViewportH]);

  // 切换文件 / 容器重挂载时回到顶部，避免沿用上一文件的滚动位置。
  useEffect(() => {
    setCodeScrollTop(0);
    if (codeScrollEl !== undefined) codeScrollEl.scrollTop = 0;
  }, [selectedPath, codeScrollEl]);

  const contentLines = splitEditorLines(content);
  // D：只读视图的可视行窗口（仅渲染 [start, end)，上下用 spacer 占位）。
  const totalCodeLines = contentLines.length;
  const codeWindow = computeLineWindow(
    codeScrollTop,
    codeViewportH,
    codeRowH,
    totalCodeLines,
    CODE_OVERSCAN,
  );
  const codeTopSpacer = codeWindow.start * codeRowH;
  const codeBottomSpacer = Math.max(0, (totalCodeLines - codeWindow.end) * codeRowH);
  const draftLines = splitEditorLines(draftContent);
  const dirty = editing && draftContent !== content;
  const activeDiagnostic = editing ? liveDiagnostic : selectedEntry?.diagnostic;
  const activeLines = editing ? draftLines : contentLines;
  // B：visibleDiagnostics 只在编辑面板渲染，只读模式算它是 O(N·D) 空转。
  // 仅编辑态才做整文件诊断扫描。
  const visibleDiagnostics =
    !editing || selectedEntry === undefined || selectedPath === undefined
      ? []
      : visibleDiagnosticsForContent(
          selectedEntry,
          activeLines,
          taskId,
          selectedPath,
          acceptedDiagnosticKeys,
          activeDiagnostic,
        );

  const acceptDiagnostic = (key: string): void => {
    if (selectedPath === undefined) return;
    setAcceptedDiagnosticKeys((prev) => {
      const next = new Set(prev);
      next.add(key);
      writeAcceptedDiagnosticKeys(taskId, selectedPath, next);
      return next;
    });
  };

  const clearAcceptedDiagnostics = (): void => {
    if (selectedPath === undefined) return;
    const next = new Set<string>();
    writeAcceptedDiagnosticKeys(taskId, selectedPath, next);
    setAcceptedDiagnosticKeys(next);
  };

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
                ...(liveDiagnostic === undefined
                  ? {}
                  : {
                      diagnostic: liveDiagnostic,
                      compile_status:
                        liveDiagnostic.status === "syntax_clean"
                          ? "passed"
                          : "failed",
                      compile_failing_lines: liveDiagnostic.failing_lines,
                      compile_error: liveDiagnostic.summary,
                    }),
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
              <>
                <div className="code-editor-edit-wrap">
                  <div
                    ref={editGutterRef}
                    className="code-editor-edit-gutter"
                    aria-hidden="true"
                  >
                    {draftLines.map((_, lineIndex) => {
                      const rawLineItems = diagnosticItemsForLine(
                        selectedEntry,
                        lineIndex,
                        liveDiagnostic,
                      );
                      const lineItems = filterAcceptedDiagnostics(
                        rawLineItems,
                        draftLines[lineIndex] ?? "",
                        taskId,
                        selectedEntry.path,
                        acceptedDiagnosticKeys,
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
                {(liveDiagnosticLoading ||
                  liveDiagnosticError !== undefined ||
                  visibleDiagnostics.length > 0 ||
                  acceptedDiagnosticKeys.size > 0) && (
                  <div className="code-editor-diagnostics">
                    <div className="code-editor-diagnostics-header">
                      <strong>
                        {t("codeViewer.diagnosticsTitle", {
                          count: visibleDiagnostics.length,
                        })}
                      </strong>
                      {liveDiagnosticLoading && (
                        <span>{t("codeViewer.liveDiagnosticPending")}</span>
                      )}
                      {acceptedDiagnosticKeys.size > 0 && (
                        <button
                          type="button"
                          className="code-editor-diagnostic-action"
                          onClick={clearAcceptedDiagnostics}
                        >
                          {t("codeViewer.clearAcceptedDiagnostics")}
                        </button>
                      )}
                    </div>
                    {liveDiagnosticError !== undefined && (
                      <div className="code-editor-diagnostic-error">
                        {t("codeViewer.liveDiagnosticError")}:{" "}
                        {liveDiagnosticError}
                      </div>
                    )}
                    {acceptedDiagnosticKeys.size > 0 && (
                      <div className="code-editor-diagnostic-muted">
                        {t("codeViewer.acceptedDiagnostics", {
                          count: acceptedDiagnosticKeys.size,
                        })}
                      </div>
                    )}
                    {visibleDiagnostics.map(({ item, lineNumber, key }) => (
                      <div
                        key={key}
                        className={
                          "code-editor-diagnostic-item" +
                          diagnosticClass([item])
                        }
                      >
                        <span className="code-editor-diagnostic-line">
                          {lineNumber.toString()}
                        </span>
                        <span className="code-editor-diagnostic-message">
                          {diagnosticTitle([item])}
                        </span>
                        <button
                          type="button"
                          className="code-editor-diagnostic-action"
                          onClick={() => {
                            acceptDiagnostic(key);
                          }}
                        >
                          {t("codeViewer.acceptDiagnostic")}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div
                ref={(el) => { setCodeScrollEl(el ?? undefined); }}
                className="code-content-text"
              >
                {selectedEntry !== undefined && (
                  <div className="code-virtual-inner">
                    {/* 锚点 overlay：所有 page 锚点绝对定位在 lineIndex*rowH，
                        与窗口化的行解耦，保证 useScrollSync 始终量得到锚点。 */}
                    {codePageAnchors.map((anchor) => (
                      <span
                        key={anchor.sourcePage}
                        data-page={anchor.pageKey}
                        className="code-page-anchor"
                        style={{ top: `${(anchor.lineIndex * codeRowH).toString()}px` }}
                      />
                    ))}
                    <div
                      className="code-virtual-spacer"
                      style={{ height: `${codeTopSpacer.toString()}px` }}
                      aria-hidden="true"
                    />
                    {contentLines
                      .slice(codeWindow.start, codeWindow.end)
                      .map((line, offset) => {
                        const lineIndex = codeWindow.start + offset;
                        const rawLineItems = diagnosticItemsForLine(
                          selectedEntry,
                          lineIndex,
                        );
                        const lineItems = filterAcceptedDiagnostics(
                          rawLineItems,
                          line,
                          taskId,
                          selectedEntry.path,
                          acceptedDiagnosticKeys,
                        );
                        const lineDiagnosticClass = diagnosticClass(lineItems);
                        const lineTitle = diagnosticTitle(lineItems);
                        return (
                          <div
                            key={lineIndex}
                            className={"code-line" + lineDiagnosticClass}
                            data-line={displayLineNumber(selectedEntry, lineIndex)}
                            title={lineTitle}
                          >
                            <span className="code-line-number">
                              {displayLineNumber(selectedEntry, lineIndex)}
                            </span>
                            <code
                              className={"code-line-code" + lineDiagnosticClass}
                            >
                              {(tokenizedLines[lineIndex] ?? []).map((token, tokenIndex) => (
                                <span
                                  key={`${lineIndex.toString()}-${tokenIndex.toString()}`}
                                  className={`code-token code-token-${token.kind}`}
                                >
                                  {token.text}
                                </span>
                              ))}
                            </code>
                          </div>
                        );
                      })}
                    <div
                      className="code-virtual-spacer"
                      style={{ height: `${codeBottomSpacer.toString()}px` }}
                      aria-hidden="true"
                    />
                  </div>
                )}
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
