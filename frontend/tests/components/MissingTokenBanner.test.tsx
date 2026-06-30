/**
 * MissingTokenBanner：服务要求 token 但本地未配置时的顶部提示横幅。
 * 默认语言 zh-CN（i18n config DEFAULT_LANGUAGE），故按中文文案断言。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MissingTokenBanner } from "../../src/components/MissingTokenBanner";
import { LanguageProvider } from "../../src/i18n";

afterEach(cleanup);

describe("MissingTokenBanner", () => {
  it("渲染缺少 token 文案与「去设置」按钮", () => {
    render(
      <LanguageProvider>
        <MissingTokenBanner onOpenSettings={vi.fn()} />
      </LanguageProvider>,
    );
    expect(screen.getByText("未设置 API Token，无法访问后端服务")).toBeTruthy();
    expect(screen.getByRole("button", { name: "去设置" })).toBeTruthy();
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("点击「去设置」触发 onOpenSettings 回调", () => {
    const onOpenSettings = vi.fn();
    render(
      <LanguageProvider>
        <MissingTokenBanner onOpenSettings={onOpenSettings} />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "去设置" }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });
});
