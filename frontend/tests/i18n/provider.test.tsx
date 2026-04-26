/**
 * LanguageProvider + useTranslation 集成测试
 */

import { act, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider, useTranslation } from "../../src/i18n";
import { STORAGE_KEY } from "../../src/i18n/config";

function wrapper({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <LanguageProvider>{children}</LanguageProvider>;
}

describe("useTranslation", () => {
  it("脱离 Provider 调用时抛错", () => {
    expect(() => renderHook(() => useTranslation())).toThrow(
      /must be used within LanguageProvider/,
    );
  });

  it("返回当前语言下的翻译", () => {
    const { result } = renderHook(() => useTranslation(), { wrapper });
    expect(result.current.t("common.cancel")).toBe("取消");
  });

  it("setLanguage 切换语言并写入 localStorage + html lang", () => {
    const { result } = renderHook(() => useTranslation(), { wrapper });

    act(() => {
      result.current.setLanguage("en");
    });
    expect(result.current.t("common.cancel")).toBe("Cancel");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("t() 替换 {param} 占位符", () => {
    const { result } = renderHook(() => useTranslation(), { wrapper });
    expect(result.current.t("taskProgress.taskLabel", { taskId: "abc" })).toBe(
      "任务：abc",
    );
  });
});

describe("LanguageProvider 渲染", () => {
  function Inner(): React.JSX.Element {
    const { t } = useTranslation();
    return <span>{t("common.cancel")}</span>;
  }

  it("在 React 树中提供 Context", () => {
    render(
      <LanguageProvider>
        <Inner />
      </LanguageProvider>,
    );
    expect(screen.getByText("取消")).toBeInTheDocument();
  });
});
