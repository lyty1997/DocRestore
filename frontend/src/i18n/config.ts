/**
 * i18n 配置与 Context 类型定义。
 *
 * 非组件导出集中在此文件，避免违反 react-refresh/only-export-components 规则。
 */

import { createContext } from "react";

import { en } from "./en";
import type { TranslationKey } from "./zh-CN";
import { zhCN } from "./zh-CN";
import { zhTW } from "./zh-TW";

/**
 * 注册所有语言：新增语言只需在此处加一行，
 * 然后在 LANGUAGE_OPTIONS 中添加对应显示名。
 *
 * locale 文件的类型为 Record<TranslationKey, string>，
 * 缺少任何 key 会导致编译错误。
 */
export const locales = {
  "zh-CN": zhCN,
  "zh-TW": zhTW,
  en,
} as const satisfies Record<string, Record<TranslationKey, string>>;

/** 从 locales 注册表自动推导语言类型——不硬编码 */
export type Language = keyof typeof locales;

export const STORAGE_KEY = "docrestore-language";
export const DEFAULT_LANGUAGE: Language = "zh-CN";

/**
 * 语言选项（标签始终使用本语言显示，方便用户识别）。
 * 新增语言时在此处添加对应条目。
 */
export const LANGUAGE_OPTIONS: readonly {
  readonly value: Language;
  readonly label: string;
}[] = [
  { value: "zh-CN", label: "简体中文" },
  { value: "zh-TW", label: "繁體中文" },
  { value: "en", label: "English" },
];

/** 翻译函数签名 */
export type TranslationFn = (
  key: string,
  params?: Record<string, string | number>,
) => string;

export interface LanguageContextValue {
  readonly language: Language;
  readonly setLanguage: (lang: Language) => void;
  readonly t: TranslationFn;
}

export const LanguageContext = createContext<LanguageContextValue | undefined>(
  undefined,
);

/** 从 localStorage 读取语言偏好 */
export function getInitialLanguage(): Language {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored !== null && stored in locales) return stored as Language;
  return DEFAULT_LANGUAGE;
}

/** 按 key 查一条翻译，缺失时回落到默认语言、再回落到 key 本身。 */
export function lookupTranslation(
  language: Language,
  key: string,
): string {
  const dict = locales[language] as Record<string, string | undefined>;
  const fallback = locales[DEFAULT_LANGUAGE] as Record<string, string | undefined>;
  return dict[key] ?? fallback[key] ?? key;
}
