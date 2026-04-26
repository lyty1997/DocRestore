/**
 * useFileUpload hook 测试：mock api/client 模块，验证状态机
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFileUpload } from "../../../src/features/task/useFileUpload";

vi.mock("../../../src/api/client", () => ({
  createUploadSession: vi.fn(),
  uploadFiles: vi.fn(),
  getUploadSessionFiles: vi.fn(),
  completeUpload: vi.fn(),
  deleteUploadSessionFile: vi.fn(),
}));

import {
  completeUpload,
  createUploadSession,
  deleteUploadSessionFile,
  getUploadSessionFiles,
  uploadFiles,
} from "../../../src/api/client";

const mocked = {
  createUploadSession: vi.mocked(createUploadSession),
  uploadFiles: vi.mocked(uploadFiles),
  getUploadSessionFiles: vi.mocked(getUploadSessionFiles),
  completeUpload: vi.mocked(completeUpload),
  deleteUploadSessionFile: vi.mocked(deleteUploadSessionFile),
};

function makeFile(name: string, content = "x"): File {
  return new File([content], name, { type: "image/jpeg" });
}

beforeEach(() => {
  for (const fn of Object.values(mocked)) fn.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useFileUpload 初始状态", () => {
  it("默认 idle 且各计数为 0", () => {
    const { result } = renderHook(() => useFileUpload());
    expect(result.current.stage).toBe("idle");
    expect(result.current.uploadedCount).toBe(0);
    expect(result.current.totalCount).toBe(0);
    expect(result.current.failedFiles).toEqual([]);
    expect(result.current.uploadedFiles).toEqual([]);
    expect(result.current.error).toBeUndefined();
  });
});

describe("useFileUpload.startUpload 成功流程", () => {
  it("分批上传后进入 completed 状态并填充 uploadedFiles", async () => {
    mocked.createUploadSession.mockResolvedValue({
      session_id: "s1",
      max_file_size_mb: 100,
      allowed_extensions: [".jpg"],
    });
    mocked.uploadFiles.mockResolvedValue({
      session_id: "s1",
      uploaded: ["a.jpg", "b.jpg"],
      total_uploaded: 2,
      failed: [],
    });
    mocked.getUploadSessionFiles.mockResolvedValue({
      session_id: "s1",
      files: [
        {
          session_id: "s1",
          file_id: "f1",
          filename: "a.jpg",
          relative_path: "a.jpg",
          size_bytes: 10,
          created_at: "2026-01-01",
        },
      ],
    });

    const { result } = renderHook(() => useFileUpload());
    act(() => {
      result.current.startUpload([makeFile("a.jpg"), makeFile("b.jpg")]);
    });
    expect(result.current.stage).toBe("uploading");

    await waitFor(() => {
      expect(result.current.stage).toBe("completed");
    });
    expect(result.current.totalCount).toBe(2);
    expect(result.current.uploadedCount).toBe(2);
    expect(result.current.uploadedFiles).toHaveLength(1);
    expect(result.current.sessionId).toBe("s1");
  });

  it("上传 0 个成功时进入 error 状态", async () => {
    mocked.createUploadSession.mockResolvedValue({
      session_id: "s1",
      max_file_size_mb: 100,
      allowed_extensions: [],
    });
    mocked.uploadFiles.mockResolvedValue({
      session_id: "s1",
      uploaded: [],
      total_uploaded: 0,
      failed: ["a.jpg"],
    });

    const { result } = renderHook(() => useFileUpload());
    act(() => {
      result.current.startUpload([makeFile("a.jpg")]);
    });
    await waitFor(() => {
      expect(result.current.stage).toBe("error");
    });
    expect(result.current.error).toBeDefined();
  });
});

describe("useFileUpload.cancelUpload", () => {
  it("AbortError 被静默吞掉，状态回到 idle", async () => {
    mocked.createUploadSession.mockResolvedValue({
      session_id: "s1",
      max_file_size_mb: 100,
      allowed_extensions: [],
    });
    mocked.uploadFiles.mockImplementation(async (_session, _files, _paths, signal) => {
      return await new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });

    const { result } = renderHook(() => useFileUpload());
    act(() => {
      result.current.startUpload([makeFile("a.jpg")]);
    });
    await waitFor(() => {
      expect(result.current.stage).toBe("uploading");
    });

    act(() => {
      result.current.cancelUpload();
    });
    expect(result.current.stage).toBe("idle");
    expect(result.current.error).toBeUndefined();
  });
});

describe("useFileUpload.finalizeUpload / deleteUploadedFile", () => {
  it("finalizeUpload 成功后写入 imageDir", async () => {
    mocked.createUploadSession.mockResolvedValue({
      session_id: "s1",
      max_file_size_mb: 100,
      allowed_extensions: [],
    });
    mocked.uploadFiles.mockResolvedValue({
      session_id: "s1",
      uploaded: ["a.jpg"],
      total_uploaded: 1,
      failed: [],
    });
    mocked.getUploadSessionFiles.mockResolvedValue({
      session_id: "s1",
      files: [
        {
          session_id: "s1",
          file_id: "f1",
          filename: "a.jpg",
          relative_path: "a.jpg",
          size_bytes: 1,
          created_at: "t",
        },
      ],
    });
    mocked.completeUpload.mockResolvedValue({
      session_id: "s1",
      image_dir: "/tmp/uploaded",
      file_count: 1,
      total_size_bytes: 1,
    });

    const { result } = renderHook(() => useFileUpload());
    act(() => {
      result.current.startUpload([makeFile("a.jpg")]);
    });
    await waitFor(() => {
      expect(result.current.stage).toBe("completed");
    });

    await act(async () => {
      await result.current.finalizeUpload();
    });
    expect(result.current.imageDir).toBe("/tmp/uploaded");
  });

  it("deleteUploadedFile 删除最后一个文件后清空 imageDir", async () => {
    mocked.createUploadSession.mockResolvedValue({
      session_id: "s1",
      max_file_size_mb: 100,
      allowed_extensions: [],
    });
    mocked.uploadFiles.mockResolvedValue({
      session_id: "s1",
      uploaded: ["a.jpg"],
      total_uploaded: 1,
      failed: [],
    });
    mocked.getUploadSessionFiles.mockResolvedValue({
      session_id: "s1",
      files: [
        {
          session_id: "s1",
          file_id: "f1",
          filename: "a.jpg",
          relative_path: "a.jpg",
          size_bytes: 1,
          created_at: "t",
        },
      ],
    });
    mocked.deleteUploadSessionFile.mockResolvedValue({
      session_id: "s1",
      file_id: "f1",
      remaining_count: 0,
    });

    const { result } = renderHook(() => useFileUpload());
    act(() => {
      result.current.startUpload([makeFile("a.jpg")]);
    });
    await waitFor(() => {
      expect(result.current.stage).toBe("completed");
    });

    await act(async () => {
      await result.current.deleteUploadedFile("f1");
    });
    expect(result.current.uploadedFiles).toHaveLength(0);
    expect(result.current.uploadedCount).toBe(0);
    expect(result.current.imageDir).toBeUndefined();
  });
});
