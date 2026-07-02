/**
 * i18n 模板插值 ``interpolate``（config.ts）测试。
 *
 * 重点回归：值里含 ``$&``/``$'``/`` $` ``/``$<name>`` 等**正则替换模式字符**时，
 * 必须按字面插入、不被 ``String.prototype.replaceAll`` 当替换模式解释（曾因用
 * 字符串替换参数导致 OCR 文件名含 ``$`` 的降级警告文案被 garble）。
 */

import { describe, expect, it } from "vitest";

import { interpolate } from "../../src/i18n/config";

describe("interpolate", () => {
  it("替换单个占位", () => {
    expect(interpolate("你好 {name}", { name: "世界" })).toBe("你好 世界");
  });

  it("同名占位全部替换（replaceAll 语义）", () => {
    expect(interpolate("{k}-{k}", { k: "z" })).toBe("z-z");
  });

  it("多个占位 + 数值参数按 String() 插入", () => {
    expect(interpolate("{a}/{b}", { a: 1, b: "2" })).toBe("1/2");
  });

  it("值含 $& / $' / $` 等替换模式字符 → 按字面插入不解释", () => {
    const value = "a$'b$&c$`d";
    expect(interpolate("文件 {file}", { file: value })).toBe(`文件 ${value}`);
  });

  it("值含 $<name> 命名捕获模式 → 按字面插入", () => {
    expect(interpolate("{v}", { v: "$<grp>" })).toBe("$<grp>");
  });

  it("值含 $$ / $1 → 按字面插入", () => {
    expect(interpolate("{v}", { v: "$$ $1" })).toBe("$$ $1");
  });

  it("无匹配占位 → 原样返回", () => {
    expect(interpolate("没有占位", { x: "y" })).toBe("没有占位");
  });

  it("空 params → 原文", () => {
    expect(interpolate("原文 {a}", {})).toBe("原文 {a}");
  });
});
