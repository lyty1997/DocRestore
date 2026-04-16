/**
 * LanguageProvider：维护 language 状态 + 构造 t() 翻译函数并注入 Context。
 *
 * 非组件导出（类型/常量/hook）拆分到 config.ts / use-translation.ts，
 * 以满足 react-refresh/only-export-components 规则。
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  LanguageContext,
  STORAGE_KEY,
  getInitialLanguage,
  lookupTranslation,
  type Language,
  type TranslationFn,
} from "./config";

export function LanguageProvider({
  children,
}: {
  readonly children: React.ReactNode;
}): React.JSX.Element {
  const [language, setLanguageState] = useState<Language>(getInitialLanguage);

  const setLanguage = useCallback((lang: Language) => {
    setLanguageState(lang);
    localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.lang = lang;
  }, []);

  /* 首次挂载时同步 lang 属性 */
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const t: TranslationFn = useCallback(
    (key, params) => {
      let text = lookupTranslation(language, key);
      if (params !== undefined) {
        for (const [k, v] of Object.entries(params)) {
          text = text.replaceAll(`{${k}}`, String(v));
        }
      }
      return text;
    },
    [language],
  );

  const value = useMemo(
    () => ({ language, setLanguage, t }),
    [language, setLanguage, t],
  );

  return (
    <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
  );
}
