/**
 * api/client 单元测试
 *
 * 重点覆盖：
 * - URL/请求体构造（fetch mock 拦截）
 * - 成功路径 zod 校验
 * - 失败路径错误抛出
 * - token 注入 URL（assets / download / WS）
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  browseDirs,
  cancelTask,
  createTask,
  createUploadSession,
  deleteTask,
  getAssetUrl,
  getDownloadUrl,
  getOcrStatus,
  getSourceImageUrl,
  getTask,
  getTaskResult,
  getTaskResults,
  getWsProgressUrl,
  listSourceImages,
  listTasks,
  retryTask,
  stageServerSources,
  updateResultMarkdown,
  uploadFiles,
  warmupOcrEngine,
} from "../../src/api/client";
import { saveApiToken } from "../../src/api/auth";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

interface QueuedResponse {
  body: unknown;
  ok: boolean;
  status: number;
  text: string;
}

const fetchCalls: FetchCall[] = [];
const responseQueue: QueuedResponse[] = [];

function mockFetchOnce(
  body: unknown,
  init: { ok?: boolean; status?: number; text?: string } = {},
): void {
  const ok = init.ok ?? true;
  responseQueue.push({
    body,
    ok,
    status: init.status ?? (ok ? 200 : 500),
    text: init.text ?? "",
  });
}

function urlToString(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

beforeEach(() => {
  fetchCalls.length = 0;
  responseQueue.length = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    fetchCalls.push({ url: urlToString(input), init });
    const queued = responseQueue.shift() ?? {
      body: {},
      ok: true,
      status: 200,
      text: "",
    };
    return Promise.resolve({
      ok: queued.ok,
      status: queued.status,
      text: () => Promise.resolve(queued.text),
      json: () => Promise.resolve(queued.body),
    } as unknown as Response);
  });
  globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("createTask", () => {
  it("POST /tasks 携带 JSON body 与 Content-Type", async () => {
    mockFetchOnce({ task_id: "t1", status: "pending" });
    const resp = await createTask({ image_dir: "/tmp/imgs" });

    expect(resp).toEqual({ task_id: "t1", status: "pending" });
    expect(fetchCalls[0]?.url).toBe("/api/v1/tasks");
    expect(fetchCalls[0]?.init?.method).toBe("POST");
    const headers = fetchCalls[0]?.init?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(fetchCalls[0]?.init?.body as string)).toEqual({
      image_dir: "/tmp/imgs",
    });
  });

  it("token 设置时附加 Authorization", async () => {
    saveApiToken("xxx");
    mockFetchOnce({ task_id: "t1", status: "pending" });
    await createTask({ image_dir: "/x" });
    const headers = fetchCalls[0]?.init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer xxx");
  });

  it("HTTP 错误时抛 Error 包含状态码", async () => {
    mockFetchOnce({}, { ok: false, status: 500, text: "boom" });
    await expect(createTask({ image_dir: "/x" })).rejects.toThrow(/HTTP 500/);
  });

  it("响应 schema 不匹配时抛错（zod）", async () => {
    mockFetchOnce({ wrong: "shape" });
    await expect(createTask({ image_dir: "/x" })).rejects.toThrow();
  });
});

describe("getTask / getTaskResult / getTaskResults", () => {
  it("getTask 命中 GET /tasks/:id", async () => {
    mockFetchOnce({ task_id: "t1", status: "processing" });
    const r = await getTask("t1");
    expect(r.status).toBe("processing");
    expect(fetchCalls[0]?.url).toBe("/api/v1/tasks/t1");
    expect(fetchCalls[0]?.init?.method).toBeUndefined();
  });

  it("getTaskResult 命中 GET /tasks/:id/result", async () => {
    mockFetchOnce({
      task_id: "t1",
      output_path: "/o",
      markdown: "# md",
    });
    const r = await getTaskResult("t1");
    expect(r.markdown).toBe("# md");
    expect(fetchCalls[0]?.url).toBe("/api/v1/tasks/t1/result");
  });

  it("getTaskResults 命中 GET /tasks/:id/results", async () => {
    mockFetchOnce({
      task_id: "t1",
      results: [{ task_id: "t1", output_path: "/o", markdown: "" }],
    });
    const r = await getTaskResults("t1");
    expect(r.results).toHaveLength(1);
  });
});

describe("listTasks query 构造", () => {
  it("无参时不带 query string", async () => {
    mockFetchOnce({ tasks: [], total: 0, page: 1, page_size: 20 });
    await listTasks();
    expect(fetchCalls[0]?.url).toBe("/api/v1/tasks");
  });

  it("传参时拼接 query string", async () => {
    mockFetchOnce({ tasks: [], total: 0, page: 2, page_size: 50 });
    await listTasks({ status: "completed", page: 2, page_size: 50 });
    expect(fetchCalls[0]?.url).toBe(
      "/api/v1/tasks?status=completed&page=2&page_size=50",
    );
  });
});

describe("cancelTask / deleteTask / retryTask", () => {
  it("cancelTask POST /:id/cancel", async () => {
    mockFetchOnce({ task_id: "t1", message: "ok" });
    await cancelTask("t1");
    expect(fetchCalls[0]?.url).toBe("/api/v1/tasks/t1/cancel");
    expect(fetchCalls[0]?.init?.method).toBe("POST");
  });

  it("deleteTask DELETE /:id", async () => {
    mockFetchOnce({ task_id: "t1" });
    await deleteTask("t1");
    expect(fetchCalls[0]?.init?.method).toBe("DELETE");
  });

  it("retryTask POST /:id/retry", async () => {
    mockFetchOnce({ task_id: "t1" });
    await retryTask("t1");
    expect(fetchCalls[0]?.url).toBe("/api/v1/tasks/t1/retry");
  });
});

describe("URL 构造（同步函数）", () => {
  it("getDownloadUrl 不带 token 时不附加", () => {
    expect(getDownloadUrl("t1")).toBe("/api/v1/tasks/t1/download");
  });

  it("getDownloadUrl 带 token 时附加 ?token=", () => {
    saveApiToken("abc");
    expect(getDownloadUrl("t1")).toBe(
      "/api/v1/tasks/t1/download?token=abc",
    );
  });

  it("getAssetUrl 拼接 assetPath", () => {
    expect(getAssetUrl("t1", "images/a.jpg")).toBe(
      "/api/v1/tasks/t1/assets/images/a.jpg",
    );
  });

  it("getSourceImageUrl 对文件名 encodeURIComponent", () => {
    expect(getSourceImageUrl("t1", "我的 图片.jpg")).toBe(
      `/api/v1/tasks/t1/source-images/${encodeURIComponent("我的 图片.jpg")}`,
    );
  });

  it("getWsProgressUrl 在 https 下使用 wss", () => {
    saveApiToken("abc");
    const original = globalThis.location;
    Object.defineProperty(globalThis, "location", {
      value: { protocol: "https:", host: "example.com:443" },
      configurable: true,
    });
    try {
      expect(getWsProgressUrl("t1")).toBe(
        "wss://example.com:443/api/v1/tasks/t1/progress?token=abc",
      );
    } finally {
      Object.defineProperty(globalThis, "location", {
        value: original,
        configurable: true,
      });
    }
  });

  it("getWsProgressUrl 在 http 下使用 ws", () => {
    expect(getWsProgressUrl("t1")).toMatch(/^ws:\/\//);
  });
});

describe("listSourceImages", () => {
  it("返回 images 数组", async () => {
    mockFetchOnce({ task_id: "t1", images: ["a.jpg", "b.png"] });
    const r = await listSourceImages("t1");
    expect(r.images).toHaveLength(2);
  });
});

describe("updateResultMarkdown", () => {
  it("PUT /tasks/:id/results/:idx 携带 JSON", async () => {
    mockFetchOnce({ task_id: "t1" });
    await updateResultMarkdown("t1", 2, "# new");
    expect(fetchCalls[0]?.url).toBe("/api/v1/tasks/t1/results/2");
    expect(fetchCalls[0]?.init?.method).toBe("PUT");
    expect(JSON.parse(fetchCalls[0]?.init?.body as string)).toEqual({
      markdown: "# new",
    });
  });
});

describe("browseDirs query", () => {
  it("无 path / includeFiles=false 时不带 query", async () => {
    mockFetchOnce({ path: "/", entries: [] });
    await browseDirs();
    expect(fetchCalls[0]?.url).toBe("/api/v1/filesystem/dirs");
  });

  it("includeFiles=true 时附加 include_files=true", async () => {
    mockFetchOnce({ path: "/x", entries: [] });
    await browseDirs("/x", true);
    expect(fetchCalls[0]?.url).toBe(
      "/api/v1/filesystem/dirs?path=%2Fx&include_files=true",
    );
  });
});

describe("stageServerSources", () => {
  it("POST /sources/server 携带 paths", async () => {
    mockFetchOnce({ image_dir: "/tmp/x", file_count: 3 });
    await stageServerSources(["/a.jpg", "/b.jpg"]);
    expect(JSON.parse(fetchCalls[0]?.init?.body as string)).toEqual({
      paths: ["/a.jpg", "/b.jpg"],
    });
  });
});

describe("Upload APIs", () => {
  it("createUploadSession POST /uploads", async () => {
    mockFetchOnce({
      session_id: "s1",
      max_file_size_mb: 100,
      allowed_extensions: [".jpg"],
    });
    const r = await createUploadSession();
    expect(r.session_id).toBe("s1");
    expect(fetchCalls[0]?.url).toBe("/api/v1/uploads");
  });

  it("uploadFiles 使用 FormData，带 paths", async () => {
    mockFetchOnce({
      session_id: "s1",
      uploaded: ["a.jpg"],
      total_uploaded: 1,
      failed: [],
    });
    const file = new File(["abc"], "a.jpg", { type: "image/jpeg" });
    await uploadFiles("s1", [file], ["dir/a.jpg"]);

    expect(fetchCalls[0]?.url).toBe("/api/v1/uploads/s1/files");
    expect(fetchCalls[0]?.init?.method).toBe("POST");

    const fd = fetchCalls[0]?.init?.body as FormData;
    expect(fd).toBeInstanceOf(FormData);
    expect(fd.getAll("files")).toHaveLength(1);
    expect(fd.getAll("paths")).toEqual(["dir/a.jpg"]);
  });

  it("uploadFiles 透传 AbortSignal", async () => {
    mockFetchOnce({
      session_id: "s1",
      uploaded: [],
      total_uploaded: 0,
      failed: [],
    });
    const ctrl = new AbortController();
    const file = new File(["x"], "x.jpg");
    await uploadFiles("s1", [file], undefined, ctrl.signal);
    expect(fetchCalls[0]?.init?.signal).toBe(ctrl.signal);
  });
});

describe("OCR APIs", () => {
  it("getOcrStatus GET /ocr/status", async () => {
    mockFetchOnce({
      current_model: "paddle",
      current_gpu: "0",
      is_ready: true,
      is_switching: false,
    });
    const r = await getOcrStatus();
    expect(r.is_ready).toBe(true);
  });

  it("warmupOcrEngine POST /ocr/warmup 带 model+gpu_id", async () => {
    mockFetchOnce({ status: "ok", message: "warmed" });
    await warmupOcrEngine("paddle", "0");
    expect(JSON.parse(fetchCalls[0]?.init?.body as string)).toEqual({
      model: "paddle",
      gpu_id: "0",
    });
  });
});
