/**
 * 上传预览图 URL 构造（#47）：token 模式下必须带 ?token=，否则 <img> 请求全 401。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { clearApiToken, saveApiToken } from "../../src/api/auth";
import {
  getAuthInfo,
  getProcessedImageUrl,
  getTaskCodeLayout,
  getTaskLayout,
  getUploadFileUrl,
} from "../../src/api/client";

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

describe("getProcessedImageUrl", () => {
  it("按原图名构造 processed-image URL（无 token、无 docDir）", () => {
    clearApiToken();
    const url = getProcessedImageUrl("t1", "IMG_0001.jpg");
    expect(url).toContain("/api/v1/tasks/t1/processed-image?");
    expect(url).toContain("name=IMG_0001.jpg");
    expect(url).not.toContain("doc_dir");
  });

  it("带 docDir → 透传 doc_dir 查询参数", () => {
    clearApiToken();
    const url = getProcessedImageUrl("t1", "IMG_0001.jpg", "subA");
    expect(url).toContain("name=IMG_0001.jpg");
    expect(url).toContain("doc_dir=subA");
  });

  it("有 token → 附加 token 供 <img> 鉴权", () => {
    saveApiToken("tok123");
    expect(getProcessedImageUrl("t1", "IMG_0001.jpg")).toContain(
      "token=tok123",
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
            { bbox: [120, 88, 2900, 240], label: "text", index: 0, text: "第一章" },
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

describe("getTaskCodeLayout", () => {
  it("404（无 sidecar）→ undefined，不抛错、不弹错误", async () => {
    const fetchMock = vi.fn(() => new Response("", { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTaskCodeLayout("t1")).resolves.toBeUndefined();
  });

  it("200 → zod 解析后的 CodeLayoutPayload（line_no/page/bbox 四元）", async () => {
    const payload = {
      files: [
        {
          path: "app/foo.py",
          lines: [
            { line_no: 1, page: "page0001.col0", bbox: [10, 20, 200, 40] },
          ],
        },
      ],
    };
    const fetchMock = vi.fn(() => Response.json(payload, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getTaskCodeLayout("t1");
    expect(result?.files[0]?.path).toBe("app/foo.py");
    expect(result?.files[0]?.lines[0]?.line_no).toBe(1);
    expect(result?.files[0]?.lines[0]?.page).toBe("page0001.col0");
    expect(result?.files[0]?.lines[0]?.bbox).toEqual([10, 20, 200, 40]);
  });

  it("docDir 经 query 透传并 URL 编码", async () => {
    const fetchMock = vi.fn(
      (_url: string) => new Response("", { status: 404 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getTaskCodeLayout("t1", "sub dir/a");
    const calledUrl = fetchMock.mock.calls[0]?.[0];
    expect(calledUrl).toContain("/tasks/t1/code-layout?doc_dir=sub%20dir%2Fa");
  });
});

describe("getAuthInfo", () => {
  it("200 → zod 解析后的 { auth_required, token_source }", async () => {
    const payload = { auth_required: true, token_source: "device_file" };
    const fetchMock = vi.fn(
      (_url: string) => Response.json(payload, { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getAuthInfo();
    expect(result.auth_required).toBe(true);
    expect(result.token_source).toBe("device_file");
    // 打到免鉴权公共端点
    const calledUrl = fetchMock.mock.calls[0]?.[0];
    expect(calledUrl).toBe("/api/v1/auth/info");
  });

  it("insecure 模式 → auth_required=false", async () => {
    const payload = { auth_required: false, token_source: "insecure" };
    const fetchMock = vi.fn(() => Response.json(payload, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getAuthInfo();
    expect(result.auth_required).toBe(false);
    expect(result.token_source).toBe("insecure");
  });

  it("非法 token_source → zod 拒绝（运行时校验生效）", async () => {
    const payload = { auth_required: true, token_source: "bogus" };
    const fetchMock = vi.fn(() => Response.json(payload, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getAuthInfo()).rejects.toThrow();
  });
});
