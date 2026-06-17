import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { UploadFileItem } from "../../src/api/schemas";
import { UploadPreviewPanel } from "../../src/components/UploadPreviewPanel";
import { LanguageProvider } from "../../src/i18n";
import { zhCN } from "../../src/i18n/zh-CN";

afterEach(cleanup);

function item(filename: string, fileId: string): UploadFileItem {
  return {
    session_id: "s1",
    file_id: fileId,
    filename,
    relative_path: filename,
    size_bytes: 10,
    created_at: "2026-06-18T00:00:00Z",
  };
}

function renderPanel(files: readonly UploadFileItem[]): void {
  render(
    <LanguageProvider>
      <UploadPreviewPanel files={files} deletingFileIds={[]} onDelete={vi.fn()} />
    </LanguageProvider>,
  );
}

/** 展开面板与第一个分组，露出文件卡片 */
async function expandAll(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByText(zhCN["uploadPreview.title"]));
  await user.click(screen.getByText(zhCN["uploadPreview.ungrouped"]));
}

describe("UploadPreviewPanel PDF 占位", () => {
  it("PDF 文件渲染占位卡（无 img，链接在新标签页打开）", async () => {
    const user = userEvent.setup();
    renderPanel([item("doc.pdf", "f1")]);
    await expandAll(user);

    // PDF 占位是链接，title 为 i18n 文案，且带 PDF 角标
    const link = screen.getByTitle(zhCN["uploadPreview.pdfDocument"]);
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(screen.getByText("PDF")).toBeTruthy();
    // PDF 卡不应渲染 <img>
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("图片文件仍渲染 <img>（alt = 文件名）", async () => {
    const user = userEvent.setup();
    renderPanel([item("photo.jpg", "f2")]);
    await expandAll(user);

    const img = screen.getByAltText("photo.jpg");
    expect(img.tagName).toBe("IMG");
    expect(screen.queryByTitle(zhCN["uploadPreview.pdfDocument"])).toBeNull();
  });
});
