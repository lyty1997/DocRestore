/**
 * i18n/errors.ts 单元测试：ApiError → LocalizedError 映射 + 渲染回退。
 */

import { describe, expect, it } from "vitest";

import { ApiError } from "../src/api/client";
import {
  fromApiError,
  fromUnknown,
  localized,
  renderLocalized,
  type LocalizedError,
} from "../src/i18n/errors";

/** 假 t() 实现：dict 命中翻译并替换 {name} 占位符；未命中返回 key 字面量 */
function makeT(dict: Record<string, string>) {
  return (key: string, params?: Record<string, string | number | readonly string[]>): string => {
    const tmpl = dict[key];
    if (tmpl === undefined) return key;
    if (params === undefined) return tmpl;
    let out = tmpl;
    for (const [k, v] of Object.entries(params)) {
      out = out.replaceAll(`{${k}}`, String(v));
    }
    return out;
  };
}

describe("fromApiError", () => {
  it("有 code 时映射到 errors.api.<lowercase>", () => {
    const err = new ApiError("任务不存在", {
      kind: "http",
      httpStatus: 404,
      code: "TASK_NOT_FOUND",
    });
    const loc = fromApiError(err);
    expect(loc.key).toBe("errors.api.task_not_found");
    expect(loc.fallback).toBe("任务不存在");
  });

  it("透传 params 与 hintKey", () => {
    const err = new ApiError("路径不是目录: /tmp/x", {
      kind: "http",
      httpStatus: 400,
      code: "BROWSE_NOT_DIR",
      params: { path: "/tmp/x" },
      hintKey: "errors.http.4xx",
    });
    const loc = fromApiError(err);
    expect(loc.params).toEqual({ path: "/tmp/x" });
    expect(loc.hintKey).toBe("errors.http.4xx");
  });

  it("无 code 但有 messageKey 时使用 messageKey", () => {
    const err = new ApiError("响应解析失败：非合法 JSON", {
      kind: "parse",
      messageKey: "errors.client.parseFailed",
      hintKey: "errors.client.parseFailedHint",
    });
    const loc = fromApiError(err);
    expect(loc.key).toBe("errors.client.parseFailed");
    expect(loc.hintKey).toBe("errors.client.parseFailedHint");
  });

  it("既无 code 也无 messageKey 时回退 errors.unknown", () => {
    const err = new ApiError("不可知错误", { kind: "network" });
    const loc = fromApiError(err);
    expect(loc.key).toBe("errors.unknown");
    expect(loc.fallback).toBe("不可知错误");
  });
});

describe("fromUnknown", () => {
  it("ApiError 走 fromApiError 路径", () => {
    const err = new ApiError("X", {
      kind: "http", httpStatus: 404, code: "TASK_NOT_FOUND",
    });
    expect(fromUnknown(err).key).toBe("errors.api.task_not_found");
  });

  it("普通 Error 用 fallbackKey + message", () => {
    const loc = fromUnknown(new Error("oops"), "errors.task.runFailed");
    expect(loc.key).toBe("errors.task.runFailed");
    expect(loc.fallback).toBe("oops");
  });

  it("非 Error 类型 stringify 后塞 fallback", () => {
    const loc = fromUnknown(42, "errors.unknown");
    expect(loc.key).toBe("errors.unknown");
    expect(loc.fallback).toBe("42");
  });
});

describe("localized", () => {
  it("无 params 时仅含 key", () => {
    expect(localized("errors.task.runFailed")).toEqual({
      key: "errors.task.runFailed",
    });
  });

  it("传 params 时附带", () => {
    expect(localized("errors.x", { reason: "oom" })).toEqual({
      key: "errors.x",
      params: { reason: "oom" },
    });
  });
});

describe("renderLocalized", () => {
  it("命中字典 + 占位符替换", () => {
    const t = makeT({ "errors.api.task_action_conflict": "状态冲突：{reason}" });
    const result = renderLocalized(
      { key: "errors.api.task_action_conflict", params: { reason: "已完成" } },
      t,
    );
    expect(result).toBe("状态冲突：已完成");
  });

  it("字典未命中且有 fallback → 用 fallback", () => {
    const t = makeT({});
    const err: LocalizedError = {
      key: "errors.api.never_seen",
      fallback: "中文兜底",
    };
    expect(renderLocalized(err, t)).toBe("中文兜底");
  });

  it("字典未命中且无 fallback → 返回 key 字面量", () => {
    const t = makeT({});
    expect(renderLocalized({ key: "errors.foo" }, t)).toBe("errors.foo");
  });

  it("有 hintKey 时主信息 + hint 用换行拼接", () => {
    const t = makeT({
      "errors.task.runFailed": "任务失败",
      "errors.http.5xx": "后端错误。",
    });
    const result = renderLocalized(
      { key: "errors.task.runFailed", hintKey: "errors.http.5xx" },
      t,
    );
    expect(result).toBe("任务失败\n后端错误。");
  });
});
