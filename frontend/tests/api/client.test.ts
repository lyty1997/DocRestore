/**
 * 上传预览图 URL 构造（#47）：token 模式下必须带 ?token=，否则 <img> 请求全 401。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { clearApiToken, saveApiToken } from "../../src/api/auth";
import { getTaskLayout, getUploadFileUrl } from "../../src/api/client";

afterEach(() => {
  clearApiToken();
  vi.unstubAllGlobals();
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

describe("getTaskLayout", () => {
  it("404（无 sidecar）→ undefined，不抛错、不弹错误", async () => {
    const fetchMock = vi.fn(
      () => new Response("", { status: 404 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTaskLayout("t1")).resolves.toBeUndefined();
  });

  it("200 → zod 解析后的 LayoutPayload（bbox 四元、image_size 二元）", async () => {
    const payload = {
      pages: [
        {
          filename: "IMG_0001.jpg",
          image_size: [3024, 4032],
          blocks: [
            { bbox: [120, 88, 2900, 240], label: "text", text: "第一章" },
          ],
        },
      ],
    };
    const fetchMock = vi.fn(() => Response.json(payload, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getTaskLayout("t1");
    expect(result?.pages[0]?.filename).toBe("IMG_0001.jpg");
    expect(result?.pages[0]?.image_size).toEqual([3024, 4032]);
    expect(result?.pages[0]?.blocks[0]?.bbox).toEqual([120, 88, 2900, 240]);
  });

  it("docDir 经 query 透传并 URL 编码", async () => {
    const fetchMock = vi.fn(
      (_url: string) => new Response("", { status: 404 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getTaskLayout("t1", "sub dir/a");
    const calledUrl = fetchMock.mock.calls[0]?.[0];
    expect(calledUrl).toContain("/tasks/t1/layout?doc_dir=sub%20dir%2Fa");
  });
});
