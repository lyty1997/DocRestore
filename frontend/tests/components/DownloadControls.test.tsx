/**
 * DownloadControls 附加导出格式选择测试（Epic D · D1）。
 *
 * 验证：默认无勾选 → 纯 zip 链接；勾选 Word/PDF → 下载链接拼上 ?formats=docx[,pdf]。
 * 断言走下载按钮 href，不依赖具体 token（测试环境无 token，href 为纯路径）。
 */

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DownloadControls } from "../../src/components/DownloadControls";
import { LanguageProvider } from "../../src/i18n";

afterEach(cleanup);

function renderControls(): ReturnType<typeof render> {
  return render(
    <LanguageProvider>
      <DownloadControls taskId="t1" downloadLabelKey="taskResult.downloadZip" />
    </LanguageProvider>,
  );
}

function downloadHref(container: HTMLElement): string {
  return container.querySelector("a.download-btn")?.getAttribute("href") ?? "";
}

describe("DownloadControls 附加导出格式", () => {
  it("默认无勾选：下载链接为纯 zip（无 formats）", () => {
    const { container } = renderControls();
    expect(downloadHref(container)).toBe("/api/v1/tasks/t1/download");
  });

  it("勾选 Word：下载链接带 formats=docx", () => {
    const { container, getByRole } = renderControls();
    fireEvent.click(getByRole("checkbox", { name: "Word" }));
    expect(decodeURIComponent(downloadHref(container))).toContain(
      "formats=docx",
    );
  });

  it("勾选 Word+PDF：formats=docx,pdf", () => {
    const { container, getByRole } = renderControls();
    fireEvent.click(getByRole("checkbox", { name: "Word" }));
    fireEvent.click(getByRole("checkbox", { name: "PDF" }));
    expect(decodeURIComponent(downloadHref(container))).toContain(
      "formats=docx,pdf",
    );
  });

  it("取消勾选后回到纯 zip", () => {
    const { container, getByRole } = renderControls();
    const word = getByRole("checkbox", { name: "Word" });
    fireEvent.click(word);
    fireEvent.click(word);
    expect(downloadHref(container)).toBe("/api/v1/tasks/t1/download");
  });
});
