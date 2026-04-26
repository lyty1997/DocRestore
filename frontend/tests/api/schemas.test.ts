/**
 * api/schemas 校验测试
 *
 * 重点覆盖：
 * - 必填字段缺失抛错
 * - 可选/可空字段允许 null/undefined
 * - 嵌套对象/数组的递归校验
 */

/* eslint-disable unicorn/no-null -- 测试 schema 对 null 字段的接受能力 */

import { describe, expect, it } from "vitest";

import {
  ActionResponseSchema,
  BrowseDirsResponseSchema,
  CreateTaskResponseSchema,
  OcrStatusResponseSchema,
  ProgressResponseSchema,
  TaskListResponseSchema,
  TaskResponseSchema,
  TaskResultResponseSchema,
  TaskResultsResponseSchema,
  UploadCompleteResponseSchema,
  UploadFilesResponseSchema,
  UploadSessionResponseSchema,
} from "../../src/api/schemas";

describe("ProgressResponseSchema", () => {
  it("校验合法的进度对象", () => {
    const ok = ProgressResponseSchema.parse({
      stage: "ocr",
      current: 1,
      total: 10,
      percent: 10,
      message: "进行中",
    });
    expect(ok.stage).toBe("ocr");
  });

  it("缺字段时抛错", () => {
    expect(() => ProgressResponseSchema.parse({ stage: "x" })).toThrow();
  });
});

describe("CreateTaskResponseSchema", () => {
  it("解析 task_id + status", () => {
    const r = CreateTaskResponseSchema.parse({ task_id: "abc", status: "pending" });
    expect(r.task_id).toBe("abc");
  });

  it("拒绝错误类型", () => {
    expect(() =>
      CreateTaskResponseSchema.parse({ task_id: 1, status: "p" }),
    ).toThrow();
  });
});

describe("TaskResponseSchema", () => {
  it("progress 与 error 允许 null/undefined", () => {
    const r = TaskResponseSchema.parse({
      task_id: "t",
      status: "processing",
      progress: null,
      error: null,
    });
    expect(r.progress).toBeNull();
  });

  it("有 progress 时进行嵌套校验", () => {
    const r = TaskResponseSchema.parse({
      task_id: "t",
      status: "processing",
      progress: {
        stage: "ocr",
        current: 0,
        total: 1,
        percent: 0,
        message: "",
      },
    });
    expect(r.progress?.stage).toBe("ocr");
  });
});

describe("TaskResultResponseSchema / TaskResultsResponseSchema", () => {
  it("校验单文档结果", () => {
    const r = TaskResultResponseSchema.parse({
      task_id: "t",
      output_path: "/tmp/out",
      markdown: "# title",
      doc_title: "标题",
      doc_dir: "doc1",
    });
    expect(r.markdown).toBe("# title");
  });

  it("doc_title / doc_dir 可缺省", () => {
    const r = TaskResultResponseSchema.parse({
      task_id: "t",
      output_path: "/tmp/out",
      markdown: "",
    });
    expect(r.doc_title).toBeUndefined();
  });

  it("多文档结果递归校验数组", () => {
    const r = TaskResultsResponseSchema.parse({
      task_id: "t",
      results: [
        { task_id: "t", output_path: "/a", markdown: "" },
        { task_id: "t", output_path: "/b", markdown: "" },
      ],
    });
    expect(r.results).toHaveLength(2);
  });
});

describe("TaskListResponseSchema", () => {
  it("校验分页响应", () => {
    const r = TaskListResponseSchema.parse({
      tasks: [
        {
          task_id: "1",
          status: "completed",
          image_dir: "/i",
          output_dir: "/o",
          error: null,
          created_at: "2026-01-01",
          result_count: 3,
          pii_enable: true,
          ocr_model: "paddle-ocr/ppocr-v4",
          llm_model: "openai/gpt-4o",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });
    expect(r.tasks[0]?.result_count).toBe(3);
    expect(r.tasks[0]?.pii_enable).toBe(true);
    expect(r.tasks[0]?.ocr_model).toBe("paddle-ocr/ppocr-v4");
    expect(r.tasks[0]?.llm_model).toBe("openai/gpt-4o");
  });

  it("缺省 pii_enable / ocr_model / llm_model 时回落到默认值（兼容老后端）", () => {
    const r = TaskListResponseSchema.parse({
      tasks: [
        {
          task_id: "legacy",
          status: "completed",
          image_dir: "/i",
          output_dir: "/o",
          created_at: "2026-01-01",
          result_count: 0,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    });
    expect(r.tasks[0]?.pii_enable).toBe(false);
    expect(r.tasks[0]?.ocr_model).toBe("");
    expect(r.tasks[0]?.llm_model).toBe("");
  });
});

describe("ActionResponseSchema", () => {
  it("message 可缺省", () => {
    const r = ActionResponseSchema.parse({ task_id: "t" });
    expect(r.task_id).toBe("t");
  });
});

describe("BrowseDirsResponseSchema", () => {
  it("entries 中的 size_bytes / image_count 允许 null", () => {
    const r = BrowseDirsResponseSchema.parse({
      path: "/tmp",
      parent: null,
      entries: [
        { name: "a.jpg", is_dir: false, size_bytes: 1024 },
        { name: "sub", is_dir: true, image_count: 5 },
        { name: "b.png", is_dir: false, size_bytes: null, image_count: null },
      ],
    });
    expect(r.entries).toHaveLength(3);
  });
});

describe("Upload schemas", () => {
  it("UploadSessionResponseSchema", () => {
    const r = UploadSessionResponseSchema.parse({
      session_id: "s",
      max_file_size_mb: 100,
      allowed_extensions: [".jpg", ".png"],
    });
    expect(r.allowed_extensions).toContain(".jpg");
  });

  it("UploadFilesResponseSchema", () => {
    const r = UploadFilesResponseSchema.parse({
      session_id: "s",
      uploaded: ["a.jpg"],
      total_uploaded: 1,
      failed: [],
    });
    expect(r.total_uploaded).toBe(1);
  });

  it("UploadCompleteResponseSchema", () => {
    const r = UploadCompleteResponseSchema.parse({
      session_id: "s",
      image_dir: "/tmp/x",
      file_count: 3,
      total_size_bytes: 999,
    });
    expect(r.image_dir).toBe("/tmp/x");
  });
});

describe("OcrStatusResponseSchema", () => {
  it("校验布尔字段", () => {
    const r = OcrStatusResponseSchema.parse({
      current_model: "paddle",
      current_gpu: "0",
      is_ready: true,
      is_switching: false,
    });
    expect(r.is_ready).toBe(true);
  });

  it("布尔字段缺失抛错", () => {
    expect(() =>
      OcrStatusResponseSchema.parse({
        current_model: "paddle",
        current_gpu: "0",
      }),
    ).toThrow();
  });
});
