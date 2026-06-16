/**
 * 预览渲染的 HTML sanitize 白名单（#46）。
 *
 * OCR/LLM 产出的 markdown 含不可信内联 HTML，`rehype-raw` 透传后必须显式过滤
 * （事件处理器 / `javascript:` 协议 / `<script>` 等），不依赖 React 隐式转义。
 *
 * 在 GitHub 默认 schema（`defaultSchema`）基础上，额外放行页锚点 `span` 的
 * `className` 与 `data-page`（hast 属性名 `dataPage`）——滚动同步（useScrollSync）
 * 按 `[data-page]` 选元素，默认 schema 会把二者剥掉导致锚点失效、同步滚动失灵。
 */

import { defaultSchema } from "rehype-sanitize";

export const PREVIEW_SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [
      ...(defaultSchema.attributes?.span ?? []),
      "className",
      "dataPage",
    ],
  },
};
