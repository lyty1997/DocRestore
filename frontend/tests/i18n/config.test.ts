/**
 * i18n/config 工具函数测试
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_LANGUAGE,
  STORAGE_KEY,
  getInitialLanguage,
  lookupTranslation,
} from "../../src/i18n/config";

describe("getInitialLanguage", () => {
  it("无存储时返回默认语言", () => {
    expect(getInitialLanguage()).toBe(DEFAULT_LANGUAGE);
  });

  it("已存合法语言时返回该语言", () => {
    localStorage.setItem(STORAGE_KEY, "en");
    expect(getInitialLanguage()).toBe("en");
  });

  it("存了非法值时回落默认", () => {
    localStorage.setItem(STORAGE_KEY, "fr-FR");
    expect(getInitialLanguage()).toBe(DEFAULT_LANGUAGE);
  });
});

describe("lookupTranslation", () => {
  it("命中目标语言", () => {
    expect(lookupTranslation("en", "common.cancel")).toBe("Cancel");
  });

  it("默认语言（zh-CN）正常返回", () => {
    expect(lookupTranslation("zh-CN", "common.cancel")).toBe("取消");
  });

  it("某语言缺 key 时回落默认语言", () => {
    /* 通常我们 schema 强制全 key，但这条路径仍要可靠 */
    const result = lookupTranslation(
      "en",
      "this.key.never.exists.xx" as unknown as string,
    );
    expect(result).toBe("this.key.never.exists.xx");
  });
});
