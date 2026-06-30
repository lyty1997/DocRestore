/**
 * useAuthStatus：拉取 /auth/info + 读本地 token，派生 needsToken。
 *
 * 关键不变量：
 * 1. 服务要求 token 且本地无 token → needsToken=true（提示设置）
 * 2. 服务要求 token 但本地已有 token → needsToken=false（不打扰）
 * 3. insecure（auth_required=false）→ needsToken=false（即便无 token）
 * 4. /auth/info 拉取失败 → authRequired=undefined，needsToken=false（不误报）
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearApiToken, saveApiToken } from "../../src/api/auth";
import { getAuthInfo } from "../../src/api/client";
import { useAuthStatus } from "../../src/hooks/useAuthStatus";

vi.mock("../../src/api/client", () => ({
  getAuthInfo: vi.fn(),
}));

const getAuthInfoMock = vi.mocked(getAuthInfo);

beforeEach(() => {
  vi.clearAllMocks();
  clearApiToken();
});
afterEach(() => { clearApiToken(); });

describe("useAuthStatus", () => {
  it("要求 token 且本地无 token → needsToken=true", async () => {
    getAuthInfoMock.mockResolvedValue({
      auth_required: true,
      token_source: "device_file",
    });
    const { result } = renderHook(() => useAuthStatus());
    await waitFor(() => { expect(result.current.loading).toBe(false); });
    expect(result.current.authRequired).toBe(true);
    expect(result.current.tokenSource).toBe("device_file");
    expect(result.current.needsToken).toBe(true);
  });

  it("要求 token 但本地已有 token → needsToken=false", async () => {
    saveApiToken("existing-tok");
    getAuthInfoMock.mockResolvedValue({
      auth_required: true,
      token_source: "device_file",
    });
    const { result } = renderHook(() => useAuthStatus());
    await waitFor(() => { expect(result.current.loading).toBe(false); });
    expect(result.current.needsToken).toBe(false);
  });

  it("insecure（auth_required=false）→ needsToken=false", async () => {
    getAuthInfoMock.mockResolvedValue({
      auth_required: false,
      token_source: "insecure",
    });
    const { result } = renderHook(() => useAuthStatus());
    await waitFor(() => { expect(result.current.loading).toBe(false); });
    expect(result.current.needsToken).toBe(false);
  });

  it("/auth/info 失败 → authRequired=undefined，needsToken=false（不误报）", async () => {
    getAuthInfoMock.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useAuthStatus());
    await waitFor(() => { expect(result.current.loading).toBe(false); });
    expect(result.current.authRequired).toBeUndefined();
    expect(result.current.needsToken).toBe(false);
  });
});
