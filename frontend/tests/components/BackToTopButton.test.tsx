/**
 * BackToTopButton 组件测试
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BackToTopButton } from "../../src/components/BackToTopButton";
import { LanguageProvider } from "../../src/i18n";

describe("BackToTopButton", () => {
  it("渲染带 aria-label 的按钮", () => {
    render(
      <LanguageProvider>
        <BackToTopButton />
      </LanguageProvider>,
    );
    expect(screen.getByRole("button", { name: "回到顶部" })).toBeInTheDocument();
  });

  it("点击后调用 scrollTo(top:0, behavior:smooth)", async () => {
    const scrollToSpy = vi
      .spyOn(globalThis, "scrollTo")
      .mockImplementation(() => {
        /* noop */
      });
    const user = userEvent.setup();
    render(
      <LanguageProvider>
        <BackToTopButton />
      </LanguageProvider>,
    );
    await user.click(screen.getByRole("button", { name: "回到顶部" }));
    expect(scrollToSpy).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
  });
});
