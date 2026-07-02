/**
 * TokenSettings：API Token 配置弹窗 + 按 token 来源定制的"如何获取"指引。
 * 默认语言 zh-CN，按中文文案断言；token 持久化走真实 localStorage（每例前清空）。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearApiToken, loadApiToken } from "../../src/api/auth";
import { TokenSettings } from "../../src/components/TokenSettings";
import { LanguageProvider } from "../../src/i18n";

const DEVICE_CMD = "cat ~/.config/docrestore/device_token";
const ENV_STEP2 =
  "向部署该服务的人索取该 token，或查看启动配置，复制后粘贴到下方保存";

function renderSettings(
  props: Partial<React.ComponentProps<typeof TokenSettings>> = {},
): void {
  render(
    <LanguageProvider>
      <TokenSettings onClose={vi.fn()} {...props} />
    </LanguageProvider>,
  );
}

beforeEach(() => { clearApiToken(); });
afterEach(cleanup);

describe("TokenSettings 获取指引", () => {
  it("device_file 来源：展示 cat 命令、不显示 env 步骤", () => {
    renderSettings({ authRequired: true, tokenSource: "device_file" });
    expect(screen.getByText("如何获取 Token？")).toBeTruthy();
    expect(screen.getByText(DEVICE_CMD)).toBeTruthy();
    expect(screen.queryByText(ENV_STEP2)).toBeNull();
  });

  it("env 来源：展示 env 索取步骤、不显示 cat 命令", () => {
    renderSettings({ authRequired: true, tokenSource: "env" });
    expect(screen.getByText("如何获取 Token？")).toBeTruthy();
    expect(screen.getByText(ENV_STEP2)).toBeTruthy();
    expect(screen.queryByText(DEVICE_CMD)).toBeNull();
  });

  it("缺省 token 来源（undefined）回退按 device_file 指引", () => {
    renderSettings({ authRequired: true });
    expect(screen.getByText(DEVICE_CMD)).toBeTruthy();
  });

  it("token 来源为 unknown 时也回退到 device 指引（展示 cat 命令）", () => {
    renderSettings({ authRequired: true, tokenSource: "unknown" });
    expect(screen.getByText(DEVICE_CMD)).toBeTruthy();
    expect(screen.queryByText(ENV_STEP2)).toBeNull();
  });

  it("authRequired=false（insecure）：只显示无需设置提示，不显示获取指引", () => {
    renderSettings({ authRequired: false, tokenSource: "insecure" });
    expect(
      screen.getByText("当前服务运行在本机无鉴权模式，无需设置 Token。"),
    ).toBeTruthy();
    expect(screen.queryByText("如何获取 Token？")).toBeNull();
  });
});

describe("TokenSettings 保存/清除", () => {
  it("输入并保存 token → 持久化、获取指引隐藏、显示遮蔽 token", () => {
    renderSettings({ authRequired: true, tokenSource: "device_file" });
    const input = screen.getByPlaceholderText("粘贴 API Token");
    fireEvent.change(input, { target: { value: "my-secret-token-123" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(loadApiToken()).toBe("my-secret-token-123");
    // 已保存 → "如何获取"指引隐藏，出现清除按钮
    expect(screen.queryByText("如何获取 Token？")).toBeNull();
    expect(screen.getByRole("button", { name: "清除" })).toBeTruthy();
  });
});
