/**
 * TokenSettings 组件测试
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TokenSettings } from "../../src/components/TokenSettings";
import { LanguageProvider } from "../../src/i18n";

function renderSettings(): { onClose: ReturnType<typeof vi.fn> } {
  const onClose = vi.fn();
  render(
    <LanguageProvider>
      <TokenSettings onClose={onClose} />
    </LanguageProvider>,
  );
  return { onClose };
}

describe("TokenSettings 未保存状态", () => {
  it("渲染输入框 + 保存按钮（按钮初始 disabled）", () => {
    renderSettings();
    const input = screen.getByPlaceholderText("粘贴 API Token");
    expect(input).toBeInTheDocument();
    const saveBtn = screen.getByRole("button", { name: "保存" });
    expect(saveBtn).toBeDisabled();
  });

  it("输入后启用保存按钮，点击后写入 localStorage 并切到已保存视图", async () => {
    const user = userEvent.setup();
    renderSettings();
    const input = screen.getByPlaceholderText("粘贴 API Token");
    await user.type(input, "secret-token-1234");

    const saveBtn = screen.getByRole("button", { name: "保存" });
    expect(saveBtn).not.toBeDisabled();
    await user.click(saveBtn);

    expect(localStorage.getItem("docrestore_api_token")).toBe(
      "secret-token-1234",
    );
    /* 切换到已保存视图：出现 Clear 按钮 */
    expect(screen.getByRole("button", { name: "清除" })).toBeInTheDocument();
  });

  it("Enter 键触发保存", async () => {
    const user = userEvent.setup();
    renderSettings();
    const input = screen.getByPlaceholderText("粘贴 API Token");
    await user.type(input, "tok123abc{Enter}");
    expect(localStorage.getItem("docrestore_api_token")).toBe("tok123abc");
  });
});

describe("TokenSettings 已保存状态", () => {
  it("渲染掩码（保留前 4 + 后 4）", () => {
    localStorage.setItem("docrestore_api_token", "abcd123456efgh");
    renderSettings();
    /* maskToken: 前 4 + 中间星号 + 后 4 */
    const masked = screen.getByText(/^abcd\*+efgh$/);
    expect(masked).toBeInTheDocument();
  });

  it("点击清除按钮后回到输入态", async () => {
    localStorage.setItem("docrestore_api_token", "abcd1234efgh");
    const user = userEvent.setup();
    renderSettings();

    await user.click(screen.getByRole("button", { name: "清除" }));
    expect(localStorage.getItem("docrestore_api_token")).toBeNull();
    expect(screen.getByPlaceholderText("粘贴 API Token")).toBeInTheDocument();
  });
});

describe("TokenSettings 关闭", () => {
  it("点击关闭按钮触发 onClose", async () => {
    const user = userEvent.setup();
    const { onClose } = renderSettings();
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalled();
  });
});
