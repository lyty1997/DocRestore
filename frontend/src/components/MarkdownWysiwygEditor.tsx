/**
 * Markdown WYSIWYG 编辑器（基于 Tiptap）。
 *
 * 行为：
 *   - 输入 ``value``（markdown）时把它转成 HTML 灌进 Tiptap
 *   - 内容变更时把 Tiptap 的 HTML 转回 markdown 通过 ``onChange`` 抛出
 *   - 完全不暴露 markdown 源码；UI 全部所见即所得（标题大字、列表项符号、
 *     表格边框、加粗斜体直接渲染）
 *   - 顶部有 toolbar：标题级别 / 加粗斜体 / 列表 / 表格 / 链接 / 撤销重做
 *
 * 与后端约定：
 *   - markdown 中 ``<!-- page: X -->`` 是位置锚点，必须 round-trip 保留 →
 *     在 ``markdownRoundtrip.ts`` 转换时用自定义 ``data-page-anchor`` div
 *     桥接，编辑器里以小灰条「📄 X」显示
 *   - 图片 URL：提供 ``taskId`` 时，灌入前用 ``editorImagesToAssetUrls`` 把
 *     ``images/...`` 重写成后端 asset URL（编辑视图才能渲染图），保存时用
 *     ``editorAssetUrlsToImages`` 逆变换回 ``images/...`` 再 ``onChange``
 *   - 工具栏「插入截图」：打开 ``FigureCropDialog`` 让用户从源图重新框选插图，
 *     裁出后插入（供修复被 OCR 切碎 / 缺失的插图）
 */
import "katex/dist/katex.min.css";

import {
  Node as TiptapNode,
  mergeAttributes,
  type Extensions,
} from "@tiptap/core";
import { Image } from "@tiptap/extension-image";
import { Link } from "@tiptap/extension-link";
import { Placeholder } from "@tiptap/extension-placeholder";
import {
  Table, TableCell, TableHeader, TableRow,
} from "@tiptap/extension-table";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import { StarterKit } from "@tiptap/starter-kit";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import { getAssetUrl } from "../api/client";
import {
  editorAssetUrlsToImages,
  editorImagesToAssetUrls,
} from "../features/task/markdown";
import { htmlToMarkdown, markdownToHtml } from "../features/task/markdownRoundtrip";
import { MathBlock, MathInline, insertMathNode } from "../features/task/mathNodes";
import {
  getCenterPagePosition,
  scrollToPagePosition,
  type PagePosition,
} from "../hooks/useScrollSync";
import { useTranslation } from "../i18n";
import { FigureCropDialog } from "./FigureCropDialog";

/** Tiptap 在 ``.wysiwyg-editor`` 与 ``.ProseMirror`` 之间插入的滚动容器类名。 */
const SCROLL_CONTAINER_SELECTOR = ".wysiwyg-editor-content";

/** 由 ``editor`` 反查其滚动容器（``.wysiwyg-editor-content``）。 */
function getScrollContainer(editor: Editor | null): HTMLElement | undefined {
  if (editor === null) return undefined;
  const container = editor.view.dom.closest(SCROLL_CONTAINER_SELECTOR);
  return container instanceof HTMLElement ? container : undefined;
}

/**
 * 自定义 PageAnchor 节点：把 ``<!-- page: X -->`` 锚点渲染为一行小灰条，
 * 用户能看到位置但无法误编辑（``atom: true`` 让它一整块选中删除）。
 */
const PageAnchor = TiptapNode.create({
  name: "pageAnchor",
  group: "block",
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      page: {
        default: "",
        parseHTML: (el: HTMLElement): string => el.dataset.page ?? "",
        renderHTML: (attrs: { page?: unknown }): Record<string, string> => {
          const v = attrs.page;
          return {
            "data-page": typeof v === "string" ? v : "",
          };
        },
      },
    };
  },

  parseHTML() {
    return [{ tag: "div[data-page-anchor]" }];
  },

  renderHTML({ HTMLAttributes }) {
    const page = String(HTMLAttributes["data-page"] ?? "");
    return [
      "div",
      mergeAttributes(HTMLAttributes, {
        "data-page-anchor": "",
        class: "wysiwyg-page-anchor",
      }),
      ["span", { class: "wysiwyg-page-anchor-icon" }, "📄"],
      ["span", { class: "wysiwyg-page-anchor-label" }, page],
    ];
  },
});


/**
 * 当前光标所在页（最近的前置 PageAnchor 的原图文件名）。
 *
 * 用于「插入截图」时把源图自动锚定到光标所在页：从文档开头扫到光标位置，
 * 记录途中最后一个 ``pageAnchor`` 节点的 ``page`` 属性（即 ``<!-- page: X -->``
 * 里的原图基名）。光标在首个页标记之前 → 返回 ``undefined``（由调用方回退）。
 */
function pageAtCursor(editor: Editor): string | undefined {
  const { from } = editor.state.selection;
  let page: string | undefined;
  editor.state.doc.nodesBetween(0, from, (node) => {
    if (node.type.name === "pageAnchor") {
      const value: unknown = node.attrs.page;
      if (typeof value === "string" && value.trim() !== "") {
        page = value.trim();
      }
    }
    return true;
  });
  return page;
}


/** 当前光标处于哪个 heading 级别（用于 toolbar select 的 value）。 */
function currentHeadingLevel(editor: Editor): "p" | "h1" | "h2" | "h3" | "h4" {
  if (editor.isActive("heading", { level: 1 })) return "h1";
  if (editor.isActive("heading", { level: 2 })) return "h2";
  if (editor.isActive("heading", { level: 3 })) return "h3";
  if (editor.isActive("heading", { level: 4 })) return "h4";
  return "p";
}


interface ToolbarProps {
  readonly editor: Editor;
  readonly t: (key: string) => string;
  /** 提供时显示「插入截图」按钮（仅在有 taskId 的任务编辑场景）。 */
  readonly onInsertFigure?: (() => void) | undefined;
}

function Toolbar({ editor, t, onInsertFigure }: ToolbarProps): React.JSX.Element {
  const btn = (
    label: string,
    isActive: boolean,
    onClick: () => void,
    title?: string,
  ): React.JSX.Element => (
    <button
      type="button"
      className={`wysiwyg-tb-btn ${isActive ? "active" : ""}`}
      onMouseDown={(e) => { e.preventDefault(); }}
      onClick={onClick}
      title={title}
    >
      {label}
    </button>
  );

  const insertTable = (): void => {
    editor.chain().focus().insertTable({
      rows: 3, cols: 3, withHeaderRow: true,
    }).run();
  };

  const setLink = (): void => {
    const attrs = editor.getAttributes("link") as { href?: string };
    const url = globalThis.prompt(
      t("editor.linkPrompt"), attrs.href ?? "https://",
    );
    if (url === null) return;          // 取消
    if (url === "") {
      editor.chain().focus().unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link")
      .setLink({ href: url }).run();
  };

  return (
    <div className="wysiwyg-toolbar">
      <select
        className="wysiwyg-tb-select"
        value={currentHeadingLevel(editor)}
        onChange={(e) => {
          const v = e.target.value;
          const chain = editor.chain().focus();
          if (v === "p") chain.setParagraph().run();
          else {
            const level = Number(v.slice(1)) as 1 | 2 | 3 | 4;
            chain.toggleHeading({ level }).run();
          }
        }}
      >
        <option value="p">{t("editor.paragraph")}</option>
        <option value="h1">{t("editor.h1")}</option>
        <option value="h2">{t("editor.h2")}</option>
        <option value="h3">{t("editor.h3")}</option>
        <option value="h4">{t("editor.h4")}</option>
      </select>

      <span className="wysiwyg-tb-sep" />

      {btn("B", editor.isActive("bold"),
        () => { editor.chain().focus().toggleBold().run(); },
        t("editor.bold"))}
      {btn("I", editor.isActive("italic"),
        () => { editor.chain().focus().toggleItalic().run(); },
        t("editor.italic"))}
      {btn("S", editor.isActive("strike"),
        () => { editor.chain().focus().toggleStrike().run(); },
        t("editor.strike"))}
      {btn("</>", editor.isActive("code"),
        () => { editor.chain().focus().toggleCode().run(); },
        t("editor.inlineCode"))}

      <span className="wysiwyg-tb-sep" />

      {btn("•", editor.isActive("bulletList"),
        () => { editor.chain().focus().toggleBulletList().run(); },
        t("editor.bulletList"))}
      {btn("1.", editor.isActive("orderedList"),
        () => { editor.chain().focus().toggleOrderedList().run(); },
        t("editor.orderedList"))}
      {btn("❝", editor.isActive("blockquote"),
        () => { editor.chain().focus().toggleBlockquote().run(); },
        t("editor.blockquote"))}
      {btn("─", false,
        () => { editor.chain().focus().setHorizontalRule().run(); },
        t("editor.hr"))}

      <span className="wysiwyg-tb-sep" />

      {btn("⊞", editor.isActive("table"), insertTable, t("editor.insertTable"))}
      {btn("🔗", editor.isActive("link"), setLink, t("editor.link"))}
      {onInsertFigure !== undefined
        && btn("🖼", false, onInsertFigure, t("editor.insertFigure"))}
      {btn("$x$", false,
        () => { insertMathNode(editor, false); },
        t("editor.insertMathInline"))}
      {btn("$x$▦", false,
        () => { insertMathNode(editor, true); },
        t("editor.insertMathBlock"))}

      <span className="wysiwyg-tb-sep" />

      {btn("↶", false,
        () => { editor.chain().focus().undo().run(); },
        t("editor.undo"))}
      {btn("↷", false,
        () => { editor.chain().focus().redo().run(); },
        t("editor.redo"))}
    </div>
  );
}


interface MarkdownWysiwygEditorProps {
  readonly value: string;
  readonly onChange: (markdown: string) => void;
  /** 任务 ID：提供时启用图片渲染（images/ ↔ asset URL）与「插入截图」。 */
  readonly taskId?: string | undefined;
  /** 多文档子目录：拼 asset URL 前缀（与预览 ``preprocessMarkdown`` 一致）。 */
  readonly docDir?: string | undefined;
  /** 挂载后初始滚动到的 page 位置（从预览侧带过来，进入编辑不回到顶部）。 */
  readonly initialPagePosition?: PagePosition | undefined;
  /** 滚动容器就绪/卸载时回调（卸载时无参调用），供外层绑定源图栏同步滚动。 */
  readonly onScrollContainerChange?: ((el?: HTMLElement) => void) | undefined;
}

/** 命令式句柄：供外层（离开编辑回预览时）读取编辑器当前 page 位置。 */
export interface MarkdownWysiwygEditorHandle {
  /** 当前视口中心所在的 page 位置；无页标记 / 未就绪时返回 undefined。 */
  readonly getPagePosition: () => PagePosition | undefined;
}

export const MarkdownWysiwygEditor = forwardRef<
  MarkdownWysiwygEditorHandle,
  MarkdownWysiwygEditorProps
>(function MarkdownWysiwygEditor(
  {
    value,
    onChange,
    taskId,
    docDir,
    initialPagePosition,
    onScrollContainerChange,
  },
  ref,
): React.JSX.Element {
  const { t } = useTranslation();
  const lastEmittedRef = useRef<string>("");
  const [showFigureDialog, setShowFigureDialog] = useState<boolean>(false);
  /* 打开「插入截图」时锁定的光标所在页（原图文件名），用于自动选源图。 */
  const [cursorPage, setCursorPage] = useState<string | undefined>();

  /* markdown(value，相对 images/) → 编辑器 HTML（asset URL，能渲染图）。 */
  const valueToHtml = (md: string): string =>
    markdownToHtml(
      taskId === undefined ? md : editorImagesToAssetUrls(md, taskId, docDir),
    );

  /* 编辑器 HTML → markdown(相对 images/，供 onChange / 保存)。 */
  const htmlToValue = (html: string): string => {
    const md = htmlToMarkdown(html);
    return taskId === undefined ? md : editorAssetUrlsToImages(md, taskId);
  };

  const extensions: Extensions = [
    StarterKit.configure({
      heading: { levels: [1, 2, 3, 4] },
    }),
    Image.configure({ inline: false, allowBase64: false }),
    Link.configure({ openOnClick: false, autolink: true }),
    Table.configure({ resizable: false }),
    TableRow,
    TableHeader,
    TableCell,
    Placeholder.configure({ placeholder: t("editor.placeholder") }),
    PageAnchor,
    MathInline,
    MathBlock,
  ];

  const editor = useEditor({
    extensions,
    content: valueToHtml(value),
    editorProps: {
      attributes: {
        class: "wysiwyg-prose",
        spellcheck: "false",
      },
    },
    onUpdate: ({ editor: ed }) => {
      const md = htmlToValue(ed.getHTML());
      lastEmittedRef.current = md;
      onChange(md);
    },
  });

  /* 外部 value 变化时（例如切换文档 tab）同步到编辑器；
     但用户自己在编辑时 onUpdate 已经把 md 写进 lastEmittedRef，
     此时 value === lastEmittedRef → 不重新 setContent，避免光标跳。 */
  useEffect(() => {
    if (value === lastEmittedRef.current) return;
    editor.commands.setContent(valueToHtml(value), { emitUpdate: false });
    lastEmittedRef.current = value;
    // valueToHtml 依赖 taskId/docDir，但二者随 value（切文档）一起变化，
    // 故只在 value 变时同步即可，无需进依赖数组（避免每次渲染重置内容）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, value]);

  /* 进入编辑时滚到预览带过来的 page 位置（不回到顶部）。编辑器就绪后用双 rAF
     等 ProseMirror 把 [data-page] 锚点渲染并布局完再落位。 */
  useEffect(() => {
    if (initialPagePosition === undefined) return;
    const container = getScrollContainer(editor);
    if (container === undefined) return;
    let raf2: number | undefined;
    const raf1 = globalThis.requestAnimationFrame(() => {
      raf2 = globalThis.requestAnimationFrame(() => {
        scrollToPagePosition(container, initialPagePosition);
      });
    });
    return () => {
      globalThis.cancelAnimationFrame(raf1);
      if (raf2 !== undefined) globalThis.cancelAnimationFrame(raf2);
    };
    // 仅在 editor 就绪时执行一次；initialPagePosition 进入编辑时已锁定不再变。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor]);

  /* 滚动容器就绪时通知外层（绑定源图栏同步滚动），卸载时清空解绑。
     EditorContent（子组件）的 effect 先跑、ProseMirror 视图已挂载，
     此处 closest 能拿到 .wysiwyg-editor-content。 */
  useEffect(() => {
    if (onScrollContainerChange === undefined) return;
    onScrollContainerChange(getScrollContainer(editor));
    return () => {
      onScrollContainerChange();
    };
  }, [editor, onScrollContainerChange]);

  /* 暴露当前 page 位置：离开编辑回预览时由外层读取以保位。 */
  useImperativeHandle(ref, () => ({
    getPagePosition: (): PagePosition | undefined => {
      const container = getScrollContainer(editor);
      return container === undefined
        ? undefined
        : getCenterPagePosition(container);
    },
  }), [editor]);

  /* 「插入截图」确认：assetPath 为相对 images/manual_N.jpg，
     编辑器内用 asset URL 显示（保存时 htmlToValue 逆变换回相对路径）。 */
  const handleFigureConfirm = (assetPath: string): void => {
    const prefix = docDir ? `${docDir}/` : "";
    const src =
      taskId === undefined
        ? assetPath
        : getAssetUrl(taskId, `${prefix}${assetPath}`);
    editor.chain().focus().setImage({ src }).run();
    setShowFigureDialog(false);
  };

  return (
    <div className="wysiwyg-editor">
      <Toolbar
        editor={editor}
        t={t}
        onInsertFigure={
          taskId === undefined
            ? undefined
            : () => {
                // 在打开对话框前锁定光标所在页，供对话框自动选中对应源图
                setCursorPage(pageAtCursor(editor));
                setShowFigureDialog(true);
              }
        }
      />
      <EditorContent editor={editor} className="wysiwyg-editor-content" />
      {showFigureDialog && taskId !== undefined && (
        <FigureCropDialog
          taskId={taskId}
          docDir={docDir}
          cursorPage={cursorPage}
          onConfirm={handleFigureConfirm}
          onClose={() => { setShowFigureDialog(false); }}
        />
      )}
    </div>
  );
});

MarkdownWysiwygEditor.displayName = "MarkdownWysiwygEditor";
