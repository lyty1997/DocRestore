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
  interpolate,
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
      const text = lookupTranslation(language, key);
      // 插值逻辑（含 $ 转义防护）抽到 config.interpolate，便于单测锁定回归。
      return params === undefined ? text : interpolate(text, params);
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
