/**
 * useTranslation hook：从 LanguageContext 获取当前语言与 t() 翻译函数。
 */

import { useContext } from "react";

import { LanguageContext, type LanguageContextValue } from "./config";

export function useTranslation(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (ctx === undefined) {
    throw new Error("useTranslation must be used within LanguageProvider");
  }
  return ctx;
}
