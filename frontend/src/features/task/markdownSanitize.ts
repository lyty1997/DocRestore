/**
 * 预览渲染的插件链与 HTML sanitize 白名单（#46 + 数学公式渲染）。
 *
 * 文档模式与 PPT 模式预览共用同一套配置（DocCodePreview）。集中在此导出，
 * 组件与测试引用同一份，避免插件顺序/ schema 漂移。
 *
 * sanitize：OCR/LLM 产出的 markdown 含不可信内联 HTML，`rehype-raw` 透传后必须
 * 显式过滤（事件处理器 / `javascript:` 协议 / `<script>` 等）。在 GitHub 默认
 * schema 基础上额外放行：
 *   - 页锚点 `span` 的 `className` 与 `data-page`（hast 属性名 `dataPage`）：
 *     滚动同步（useScrollSync）按 `[data-page]` 选元素，默认 schema 会剥掉。
 *   - `div`/`span` 的 `className`：remark-math 把 `$$...$$` 转成
 *     `<div class="math math-display">`、`$...$` 转成 `<span class="math math-inline">`，
 *     rehype-katex 靠这两个类名识别公式占位；类名被剥掉则公式不渲染。
 *
 * 数学公式：`remark-math` 解析 `$...$` / `$$...$$` → `rehype-katex` 用 KaTeX 渲染。
 */

import type { Options } from "react-markdown";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

export const PREVIEW_SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [
      ...(defaultSchema.attributes?.span ?? []),
      "className",
      "dataPage",
    ],
    div: [...(defaultSchema.attributes?.div ?? []), "className"],
  },
};

/**
 * KaTeX 渲染选项。OCR/LLM 产出的 LaTeX 常不规范（缺 `\\` 换行、缺下标、
 * operatorname 被拆字等），`throwOnError=false` 让坏公式渲染成红字而非抛错崩页；
 * `strict=false` 容忍非标准记法。`trust` 取默认 false：禁用 `\href`/`\includegraphics`
 * 等可注入属性/链接的命令，KaTeX 从 LaTeX 串生成的 DOM 本身无 XSS。
 */
const KATEX_OPTIONS = { throwOnError: false, strict: false as const };

export const PREVIEW_REMARK_PLUGINS: NonNullable<Options["remarkPlugins"]> = [
  remarkGfm,
  remarkMath,
];

/**
 * 顺序关键：`rehypeRaw`（解析不可信 HTML）→ `rehypeSanitize`（白名单过滤）→
 * `rehypeKatex`（渲染数学）。KaTeX 放最后，使其输出的 MathML / 带样式 span 不被
 * sanitize 剥掉；用户不可信 HTML 已先过 sanitize，KaTeX 输出本身安全（见上）。
 */
export const PREVIEW_REHYPE_PLUGINS: NonNullable<Options["rehypePlugins"]> = [
  rehypeRaw,
  [rehypeSanitize, PREVIEW_SANITIZE_SCHEMA],
  [rehypeKatex, KATEX_OPTIONS],
];
