/**
 * 上传预览图 URL 构造（#47）：token 模式下必须带 ?token=，否则 <img> 请求全 401。
 */

import { afterEach, describe, expect, it } from "vitest";

import { clearApiToken, saveApiToken } from "../../src/api/auth";
import { getUploadFileUrl } from "../../src/api/client";

afterEach(() => {
  clearApiToken();
});

describe("getUploadFileUrl", () => {
  it("无 token 时返回纯路径（无鉴权模式不变）", () => {
    clearApiToken();
    expect(getUploadFileUrl("s1", "f1")).toBe(
      "/api/v1/uploads/s1/files/f1",
    );
  });

  it("有 token 时附加 ?token= 供 <img> 鉴权", () => {
    saveApiToken("tok123");
    expect(getUploadFileUrl("s1", "f1")).toBe(
      "/api/v1/uploads/s1/files/f1?token=tok123",
    );
  });
});
