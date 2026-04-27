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
 *   - 图片 URL 在编辑期间走 ``preprocessMarkdown`` 重写到后端 assets，
 *     保存前用 ``revertImageUrls`` 恢复成原 ``images/...`` 形态
 */

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
import { useEffect, useRef } from "react";

import { useTranslation } from "../i18n";
import { htmlToMarkdown, markdownToHtml } from "../features/task/markdownRoundtrip";

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
}

function Toolbar({ editor, t }: ToolbarProps): React.JSX.Element {
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
}

export function MarkdownWysiwygEditor({
  value, onChange,
}: MarkdownWysiwygEditorProps): React.JSX.Element {
  const { t } = useTranslation();
  const lastEmittedRef = useRef<string>("");

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
  ];

  const editor = useEditor({
    extensions,
    content: markdownToHtml(value),
    editorProps: {
      attributes: {
        class: "wysiwyg-prose",
        spellcheck: "false",
      },
    },
    onUpdate: ({ editor: ed }) => {
      const html = ed.getHTML();
      const md = htmlToMarkdown(html);
      lastEmittedRef.current = md;
      onChange(md);
    },
  });

  /* 外部 value 变化时（例如切换文档 tab）同步到编辑器；
     但用户自己在编辑时 onUpdate 已经把 md 写进 lastEmittedRef，
     此时 value === lastEmittedRef → 不重新 setContent，避免光标跳。 */
  useEffect(() => {
    if (value === lastEmittedRef.current) return;
    const html = markdownToHtml(value);
    editor.commands.setContent(html, { emitUpdate: false });
    lastEmittedRef.current = value;
  }, [editor, value]);

  return (
    <div className="wysiwyg-editor">
      <Toolbar editor={editor} t={t} />
      <EditorContent editor={editor} />
    </div>
  );
}
