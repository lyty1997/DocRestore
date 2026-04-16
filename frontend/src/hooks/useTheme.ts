/**
 * 主题切换 hook — 日间 / 夜间模式
 *
 * 通过 `data-theme` 属性控制 CSS 变量集。
 * 用户选择持久化到 localStorage。
 */

import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "docrestore-theme";
const ATTR = "data-theme";

/** 从 localStorage 读取已保存的主题，默认 dark。 */
function loadTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return "dark";
}

/** 将主题应用到 `<html>` 元素。 */
function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute(ATTR, theme);
}

export function useTheme(): {
  theme: Theme;
  toggleTheme: () => void;
} {
  const [theme, setTheme] = useState<Theme>(loadTheme);

  /* 初始化时立即应用 */
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
