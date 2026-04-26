/**
 * api/auth 单元测试：localStorage token 存取 + 请求/URL 注入
 */

import { afterEach, describe, expect, it } from "vitest";

import {
  appendTokenToUrl,
  clearApiToken,
  getAuthHeaders,
  loadApiToken,
  saveApiToken,
} from "../../src/api/auth";

const TOKEN_KEY = "docrestore_api_token";

afterEach(() => {
  localStorage.removeItem(TOKEN_KEY);
});

describe("loadApiToken", () => {
  it("未保存时返回空字符串", () => {
    expect(loadApiToken()).toBe("");
  });

  it("能读取 localStorage 中的 token", () => {
    localStorage.setItem(TOKEN_KEY, "abc123");
    expect(loadApiToken()).toBe("abc123");
  });
});

describe("saveApiToken", () => {
  it("写入非空 token 后能取回", () => {
    saveApiToken("  secret  ");
    expect(localStorage.getItem(TOKEN_KEY)).toBe("secret");
  });

  it("写入空白 token 时清除已有记录", () => {
    localStorage.setItem(TOKEN_KEY, "old");
    saveApiToken("   ");
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});

describe("clearApiToken", () => {
  it("能清除已保存 token", () => {
    localStorage.setItem(TOKEN_KEY, "x");
    clearApiToken();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});

describe("getAuthHeaders", () => {
  it("无 token 时返回空对象", () => {
    expect(getAuthHeaders()).toEqual({});
  });

  it("有 token 时返回 Bearer header", () => {
    saveApiToken("tok");
    expect(getAuthHeaders()).toEqual({ Authorization: "Bearer tok" });
  });
});

describe("appendTokenToUrl", () => {
  it("无 token 时原样返回", () => {
    expect(appendTokenToUrl("/api/v1/foo")).toBe("/api/v1/foo");
  });

  it("URL 无 query 时使用 ? 分隔", () => {
    saveApiToken("t/k 1");
    expect(appendTokenToUrl("/api/v1/foo")).toBe(
      "/api/v1/foo?token=t%2Fk%201",
    );
  });

  it("URL 已有 query 时使用 & 分隔", () => {
    saveApiToken("ttt");
    expect(appendTokenToUrl("/api/v1/foo?x=1")).toBe(
      "/api/v1/foo?x=1&token=ttt",
    );
  });
});
