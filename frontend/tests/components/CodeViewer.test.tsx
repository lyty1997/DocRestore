import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getCodeFileContent,
  getFilesIndex,
  updateCodeFileContent,
} from "../../src/api/client";
import type { FilesIndex } from "../../src/api/schemas";
import { CodeViewer } from "../../src/components/CodeViewer";
import { LanguageProvider } from "../../src/i18n";

vi.mock("../../src/api/client", () => ({
  getCodeFileContent: vi.fn(),
  getFilesIndex: vi.fn(),
  getSourceImageUrl: vi.fn((taskId: string, filename: string): string =>
    `/api/v1/tasks/${taskId}/source-images/${filename}`,
  ),
  updateCodeFileContent: vi.fn(),
}));

const getFilesIndexMock = vi.mocked(getFilesIndex);
const getCodeFileContentMock = vi.mocked(getCodeFileContent);
const updateCodeFileContentMock = vi.mocked(updateCodeFileContent);

function renderViewer(index: FilesIndex, content?: string): void {
  getFilesIndexMock.mockResolvedValue(index);
  getCodeFileContentMock.mockResolvedValue(
    content ??
      [
        "line 1",
        "line 2",
        "line 3",
        "line 4",
        "line 5",
      ].join("\n"),
  );
  updateCodeFileContentMock.mockResolvedValue({ task_id: "task-1" });

  render(
    <LanguageProvider>
      <CodeViewer
        taskId="task-1"
        allSourceImages={["raw/DSC1.JPG", "raw/DSC2.JPG"]}
      />
    </LanguageProvider>,
  );
}

function getRequiredElement(selector: string): Element {
  const element = document.querySelector(selector);
  if (element === null) {
    throw new Error(`找不到测试元素：${selector}`);
  }
  return element;
}

describe("CodeViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("给代码正文和原图预览写入同名 data-page 锚点", async () => {
    renderViewer([
      {
        path: "src/foo.cc",
        filename: "foo.cc",
        language: "cpp",
        source_pages: ["DSC1.col0", "DSC2.col0"],
        source_page_ranges: [
          { page: "DSC1.col0", start_line: 1, end_line: 3 },
          { page: "DSC2.col0", start_line: 4, end_line: 5 },
        ],
        line_count: 5,
        line_no_range: [1, 5],
        flags: [],
      },
    ]);

    await screen.findByText("src/foo.cc");
    await waitFor(() => {
      expect(document.querySelector(".code-content-text")).not.toBeNull();
    });

    const codeAnchor = getRequiredElement(
      '.code-content-text [data-page="DSC2.JPG"]',
    );
    const imageAnchor = getRequiredElement(
      '.code-source-images-list [data-page="DSC2.JPG"]',
    );

    expect(codeAnchor.className).toBe("code-page-anchor");
    expect(imageAnchor.getAttribute("alt")).toBe("raw/DSC2.JPG");
  });

  it("旧 files-index 没有来源页行号范围时仍按来源页顺序生成锚点", async () => {
    renderViewer([
      {
        path: "src/foo.cc",
        filename: "foo.cc",
        language: "cpp",
        source_pages: ["DSC1.col0", "DSC2.col0"],
        source_page_ranges: [],
        line_count: 5,
        line_no_range: [1, 5],
        flags: [],
      },
    ]);

    await screen.findByText("src/foo.cc");
    await waitFor(() => {
      expect(document.querySelector(".code-content-text")).not.toBeNull();
    });

    expect(
      document.querySelectorAll(".code-content-text .code-page-anchor"),
    ).toHaveLength(2);
  });

  it("渲染行号、语言着色和编译失败行", async () => {
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: ["DSC1.col0"],
          source_page_ranges: [],
          line_count: 5,
          line_no_range: [10, 14],
          flags: [],
          compile_status: "failed",
          compile_error: "foo.cc:3:1: error: invalid preprocessing directive",
          compile_failing_lines: [3],
        },
      ],
      [
        "#include <stdint.h>",
        "namespace media {",
        "return 42;",
        "}",
        "// comment",
      ].join("\n"),
    );

    await screen.findByText("src/foo.cc");

    await waitFor(() => {
      expect(document.querySelector('.code-line[data-line="12"]')).not.toBeNull();
    });
    const errorLine = getRequiredElement('.code-line[data-line="12"]');

    expect(errorLine.className).toContain("has-syntax-diagnostic");
    expect(screen.getByText("10").className).toContain("code-line-number");
    expect(screen.getByText("return").className).toContain("code-token-keyword");
    expect(screen.getByText("// comment").className).toContain(
      "code-token-comment",
    );
  });

  it("使用 diagnostic.items 渲染依赖类审查波浪线", async () => {
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: ["DSC1.col0"],
          source_page_ranges: [],
          line_count: 2,
          line_no_range: [1, 2],
          flags: [],
          compile_status: "failed",
          compile_error: "missing include",
          compile_failing_lines: [1],
          diagnostic: {
            path: "src/foo.cc",
            language: "cpp",
            status: "dependency_dirty",
            category: "dependency",
            summary: "missing include",
            failing_lines: [1],
            syntax_errors: 0,
            semantic_errors: 0,
            dependency_errors: 1,
            items: [{
              line: 1,
              column: 10,
              severity: "warn",
              category: "dependency",
              code: "missing_include",
              message: "missing.h: No such file or directory",
              source: "g++",
            }],
            tool: "g++",
            duration_ms: 1,
          },
        },
      ],
      '#include "missing.h"\nint main() { return 0; }',
    );

    await screen.findByText("src/foo.cc");

    const line = await waitFor(() =>
      getRequiredElement('.code-line[data-line="1"]'),
    );
    expect(line.className).toContain("has-dependency-diagnostic");
    expect(line.getAttribute("title")).toContain("missing.h");
  });

  it("支持在线编辑并保存当前代码文件", async () => {
    const user = userEvent.setup();
    renderViewer([
      {
        path: "src/foo.cc",
        filename: "foo.cc",
        language: "cpp",
        source_pages: [],
        source_page_ranges: [],
        line_count: 1,
        line_no_range: [1, 1],
        flags: [],
      },
    ], "return 1;");

    await screen.findByText("src/foo.cc");
    await user.click(screen.getByRole("button", { name: "编辑" }));

    const textarea = screen.getByLabelText("编辑代码文件内容");
    await user.clear(textarea);
    await user.type(textarea, "return 2;");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(updateCodeFileContentMock).toHaveBeenCalledWith(
        "task-1",
        "src/foo.cc",
        "return 2;",
      );
    });
    expect(screen.queryByLabelText("编辑代码文件内容")).toBeNull();
    expect(screen.getByText("2").className).toContain("code-token-number");
  });
});
