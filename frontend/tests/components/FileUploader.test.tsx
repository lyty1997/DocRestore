import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createUploadSession,
  getUploadSessionFiles,
  uploadFiles,
} from "../../src/api/client";
import { FileUploader } from "../../src/components/FileUploader";
import { LanguageProvider } from "../../src/i18n";
import { zhCN } from "../../src/i18n/zh-CN";

/* 只打桩上传链路的网络函数，保留其余真实实现。 */
vi.mock("../../src/api/client", async (importActual) => {
  const actual = await importActual<typeof import("../../src/api/client")>();
  return {
    ...actual,
    createUploadSession: vi.fn(),
    uploadFiles: vi.fn(),
    getUploadSessionFiles: vi.fn(),
  };
});

const mockCreate = vi.mocked(createUploadSession);
const mockUpload = vi.mocked(uploadFiles);
const mockGetFiles = vi.mocked(getUploadSessionFiles);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderUploader(): HTMLElement {
  const { container } = render(
    <LanguageProvider>
      <FileUploader onComplete={vi.fn()} disabled={false} />
    </LanguageProvider>,
  );
  return container;
}

/** 拿到“选择文件”用的 multiple file input（第一个 file input） */
function fileInput(container: HTMLElement): HTMLInputElement {
  const inputs = container.querySelectorAll<HTMLInputElement>(
    'input[type="file"]',
  );
  const first = inputs[0];
  if (first === undefined) throw new Error("file input not found");
  return first;
}

describe("FileUploader 互斥预校验", () => {
  it("图片与 PDF 混选 → 显示互斥提示且不发起上传", async () => {
    const container = renderUploader();
    const user = userEvent.setup();

    await user.upload(fileInput(container), [
      new File(["img"], "a.jpg", { type: "image/jpeg" }),
      new File(["pdf"], "b.pdf", { type: "application/pdf" }),
    ]);

    expect(
      screen.getByText(zhCN["fileUploader.mixedInputError"]),
    ).toBeTruthy();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("全 PDF 选择 → 无提示并发起上传", async () => {
    mockCreate.mockResolvedValue({
      session_id: "s1",
      max_file_size_mb: 200,
      allowed_extensions: [".pdf"],
    });
    mockUpload.mockResolvedValue({
      session_id: "s1",
      uploaded: ["doc.pdf"],
      total_uploaded: 1,
      failed: [],
    });
    mockGetFiles.mockResolvedValue({
      session_id: "s1",
      files: [
        {
          session_id: "s1",
          file_id: "f1",
          filename: "doc.pdf",
          relative_path: "doc.pdf",
          size_bytes: 10,
          created_at: "2026-06-18T00:00:00Z",
        },
      ],
    });

    const container = renderUploader();
    const user = userEvent.setup();

    await user.upload(fileInput(container), [
      new File(["pdf"], "doc.pdf", { type: "application/pdf" }),
    ]);

    await waitFor(() => {
      expect(mockCreate).toHaveBeenCalledTimes(1);
    });
    expect(
      screen.queryByText(zhCN["fileUploader.mixedInputError"]),
    ).toBeNull();
  });
});
