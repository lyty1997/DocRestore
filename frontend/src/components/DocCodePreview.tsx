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

import "katex/dist/katex.min.css";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";

import {
  getFilesIndex,
  getTaskLayout,
  isNotFoundError,
  listSourceImages,
  updateResultMarkdown,
} from "../api/client";
import type { LayoutPayload, TaskResultResponse } from "../api/schemas";
import {
  computeBlockHighlight,
  type CursorBlock,
  type SourceImageHighlight,
} from "../features/task/blockHighlight";
import { preprocessMarkdown } from "../features/task/markdown";
import { previewBlockAtPointer } from "../features/task/previewBlockAtPointer";
import {
  PREVIEW_REHYPE_PLUGINS,
  PREVIEW_REMARK_PLUGINS,
} from "../features/task/markdownSanitize";
import { filterImagesForDoc } from "../features/task/sourceImages";
import { usePreviewScrollSync } from "../hooks/usePreviewScrollSync";
import {
  getCenterPagePosition,
  scrollToPagePosition,
  type PagePosition,
} from "../hooks/useScrollSync";
import { useTranslation } from "../i18n";
import { CodeViewer } from "./CodeViewer";
import {
  MarkdownWysiwygEditor,
  type MarkdownWysiwygEditorHandle,
} from "./MarkdownWysiwygEditor";
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

  /* 预览 ↔ 编辑互切保位：
     - editStartPosition：进入编辑时从预览侧抓取，传给编辑器作初始滚动位置
     - previewRestorePosition：离开编辑（预览/保存）时从编辑器抓取，预览重挂后落位 */
  const editorRef = useRef<MarkdownWysiwygEditorHandle>(null);
  const [editStartPosition, setEditStartPosition] = useState<PagePosition>();
  const [previewRestorePosition, setPreviewRestorePosition] =
    useState<PagePosition>();

  /* 左右同步滚动：callback ref → state 触发 hook 重绑 listener */
  const [leftScrollEl, setLeftScrollEl] = useState<HTMLDivElement>();
  const [rightScrollEl, setRightScrollEl] = useState<HTMLDivElement>();
  /* 编辑模式右侧滚动容器（编辑器就绪后经 onScrollContainerChange 填入） */
  const [editorScrollEl, setEditorScrollEl] = useState<HTMLElement>();

  /* Epic E：光标块 ↔ 原图 bbox 高亮（仅文档编辑模式）。
     layout = 该文档 .layout.json 载荷；cursorBlock = 编辑器上报的光标所在块。 */
  const [layout, setLayout] = useState<LayoutPayload | undefined>();
  const [cursorBlock, setCursorBlock] = useState<CursorBlock | undefined>();
  const handleCursorBlock = useCallback(
    (block: CursorBlock | undefined): void => { setCursorBlock(block); },
    [],
  );
  /* E4：预览模式 hover 防抖定时器（与编辑器 onSelectionUpdate 同 80ms）。 */
  const previewHoverTimer = useRef<number | undefined>(undefined);
  const handlePreviewMouseMove = useCallback(
    (event: React.MouseEvent<HTMLDivElement>): void => {
      /* React 合成事件在 setTimeout 回调里 currentTarget 会被置空，先同步取出。 */
      const container = event.currentTarget;
      const target = event.target as Element | null;
      if (previewHoverTimer.current !== undefined) {
        globalThis.clearTimeout(previewHoverTimer.current);
      }
      previewHoverTimer.current = globalThis.setTimeout(() => {
        setCursorBlock(previewBlockAtPointer(target, container));
      }, 80);
    },
    [],
  );
  const handlePreviewMouseLeave = useCallback((): void => {
    if (previewHoverTimer.current !== undefined) {
      globalThis.clearTimeout(previewHoverTimer.current);
      previewHoverTimer.current = undefined;
    }
    setCursorBlock(undefined);
  }, []);

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
    selectedDoc?.markdown ?? "",
  );

  /* Epic E：文档视图（预览或编辑）且选中文档正常时取版面、做高亮（E4 起
     去掉 editMode 约束，预览模式也启用）。 */
  const canHighlight =
    viewMode === "doc" &&
    !selectedDocFailed &&
    selectedDoc !== undefined;
  const editDocDir = selectedDoc?.doc_dir;

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
      .catch((error_: unknown) => {
        if (!cancelled) setCodeAvailable(false);
        // 404 = 非代码模式（预期，不显示 toggle）；其它错误是真实失败，
        // 不能静默吞成"无代码视图"——记录便于排查后端瞬时故障。
        if (!isNotFoundError(error_)) {
          console.error("探测代码模式产物失败", error_);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  /* Epic E：进入文档编辑模式时取该文档 .layout.json（懒加载）；离开 / 切文档时
     重置。无 sidecar（非 VL / 老任务）→ getTaskLayout 返回 undefined，不高亮。 */
  useEffect(() => {
    if (!canHighlight) {
      setLayout(undefined);
      setCursorBlock(undefined);
      return;
    }
    let cancelled = false;
    getTaskLayout(taskId, editDocDir)
      .then((res) => {
        if (!cancelled) setLayout(res);
      })
      .catch(() => {
        if (!cancelled) setLayout(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, canHighlight, editDocDir]);

  /* 光标块 → 命中页 bbox 高亮（纯计算，失配 → undefined 不高亮）。 */
  const highlight = useMemo<SourceImageHighlight | undefined>(
    () => computeBlockHighlight(layout, cursorBlock),
    [layout, cursorBlock],
  );

  /* E4：编辑 ↔ 预览互切时复位光标块，避免残留上一模式的高亮（新模式首次
     交互前没有事件）。layout 不动（同文档共用）。 */
  useEffect(() => {
    setCursorBlock(undefined);
  }, [editMode]);

  /* E4：卸载时清掉 hover 防抖定时器。 */
  useEffect(
    () => () => {
      if (previewHoverTimer.current !== undefined) {
        globalThis.clearTimeout(previewHoverTimer.current);
      }
    },
    [],
  );

  usePreviewScrollSync(
    leftScrollEl,
    rightScrollEl,
    !editMode && !selectedDocFailed && viewMode === "doc",
  );

  /* 编辑模式：源图栏 ↔ 编辑器同步滚动（同一 page 锚点策略，与预览手感一致） */
  usePreviewScrollSync(
    leftScrollEl,
    editorScrollEl,
    editMode && !selectedDocFailed && viewMode === "doc",
  );

  const enterEdit = useCallback((): void => {
    if (selectedDoc !== undefined) {
      // 进入编辑前抓取预览当前所在 page 位置，传给编辑器作初始滚动（不回顶部）
      setEditStartPosition(
        rightScrollEl === undefined
          ? undefined
          : getCenterPagePosition(rightScrollEl),
      );
      setEditText(selectedDoc.markdown);
      setEditMode(true);
      setSaveError(undefined);
    }
  }, [selectedDoc, rightScrollEl]);

  /* 离开编辑。restore=true（预览/保存）时抓取编辑器当前位置，预览重挂后落回去；
     restore=false（切文档 / 切代码视图，语境已变）则清空不保位。 */
  const leaveEdit = useCallback((restore: boolean): void => {
    setPreviewRestorePosition(
      restore ? editorRef.current?.getPagePosition() : undefined,
    );
    setEditMode(false);
  }, []);

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
      leaveEdit(true);
    } catch {
      setSaveError(t("common.saveFailed"));
    } finally {
      setSaving(false);
    }
  }, [
    editText, leaveEdit, onResultsChange, results, selectedDoc, selectedIdx,
    taskId, t,
  ]);

  /* 离开编辑回到预览后，把预览滚回编辑时所在位置（双向保位的回程）。
     预览重挂 → rightScrollEl 经 callback ref 填入触发本 effect；双 rAF 等
     Markdown 锚点布局完再落位，用完即清避免劫持后续正常滚动。 */
  useEffect(() => {
    if (editMode) return;
    if (previewRestorePosition === undefined) return;
    if (rightScrollEl === undefined) return;
    const pos = previewRestorePosition;
    let raf2: number | undefined;
    const raf1 = globalThis.requestAnimationFrame(() => {
      raf2 = globalThis.requestAnimationFrame(() => {
        scrollToPagePosition(rightScrollEl, pos);
        setPreviewRestorePosition(undefined);
      });
    });
    return () => {
      globalThis.cancelAnimationFrame(raf1);
      if (raf2 !== undefined) globalThis.cancelAnimationFrame(raf2);
    };
  }, [editMode, previewRestorePosition, rightScrollEl]);

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
                if (editMode) leaveEdit(false);
                setViewMode("doc");
              }}
            >
              {t("taskDetail.viewModeDoc")}
            </button>
            <button
              type="button"
              className={`toggle-btn ${viewMode === "code" ? "active" : ""}`}
              onClick={() => {
                if (editMode) leaveEdit(false);
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
                onClick={() => { leaveEdit(true); }}
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
                if (editMode) leaveEdit(false);
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
          highlight={highlight}
          processed={layout?.processed ?? false}
          docDir={editDocDir}
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
        {/* #96：非致命软降级（VL 退本地 / PDF 缺页 / 段截断），文档可用但有降级 */}
        {!selectedDocFailed && selectedDoc.warnings.length > 0 && (
          <div className="doc-warning-banner" role="alert">
            <span className="doc-warning-icon" aria-hidden="true">⚠</span>
            <ul className="doc-warning-list">
              {selectedDoc.warnings.map((w, i) => (
                // key 带 index：同一 code 可能重复出现（如多个缺口），纯用 code 会
                // React key 冲突而丢重复项。legacy（旧任务原中文串）走 warnings.legacy
                // = "{text}" 模板，统一由 t() 渲染，组件内不直接索引 params。
                <li key={`${String(i)}-${w.code}`}>
                  {t(`taskDetail.warnings.${w.code}`, w.params)}
                </li>
              ))}
            </ul>
          </div>
        )}
        {!selectedDocFailed && editMode && (
          <div className="markdown-editor">
            <MarkdownWysiwygEditor
              ref={editorRef}
              value={editText}
              onChange={setEditText}
              taskId={taskId}
              docDir={selectedDoc.doc_dir}
              initialPagePosition={editStartPosition}
              onScrollContainerChange={setEditorScrollEl}
              onCursorBlockChange={handleCursorBlock}
            />
          </div>
        )}
        {!selectedDocFailed && !editMode && (
          <div
            ref={(el) => { setRightScrollEl(el ?? undefined); }}
            className="markdown-preview"
            onMouseMove={canHighlight ? handlePreviewMouseMove : undefined}
            onMouseLeave={canHighlight ? handlePreviewMouseLeave : undefined}
          >
            <Markdown
              remarkPlugins={PREVIEW_REMARK_PLUGINS}
              // 顺序：rehypeRaw 解析 HTML → rehypeSanitize 白名单过滤 →
              // rehypeKatex 渲染数学公式（详见 markdownSanitize.ts）
              rehypePlugins={PREVIEW_REHYPE_PLUGINS}
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
