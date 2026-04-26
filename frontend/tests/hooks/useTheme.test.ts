/**
 * useTheme hook 测试
 */

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useTheme } from "../../src/hooks/useTheme";

const STORAGE_KEY = "docrestore-theme";

describe("useTheme", () => {
  it("默认 dark 并应用到 documentElement", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("已保存 light 时初始化为 light", () => {
    localStorage.setItem(STORAGE_KEY, "light");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
  });

  it("非法值回落 dark", () => {
    localStorage.setItem(STORAGE_KEY, "foo");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
  });

  it("toggleTheme 在 light/dark 间切换并持久化", () => {
    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.toggleTheme();
    });
    expect(result.current.theme).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");

    act(() => {
      result.current.toggleTheme();
    });
    expect(result.current.theme).toBe("dark");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
  });
});
