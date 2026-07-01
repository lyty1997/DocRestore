import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  diagnoseCodeFileContent,
  getCodeFileContent,
  getFilesIndex,
  getTaskCodeLayout,
  updateCodeFileContent,
} from "../../src/api/client";
import type { FilesIndex } from "../../src/api/schemas";
import { CodeViewer } from "../../src/components/CodeViewer";
import { LanguageProvider } from "../../src/i18n";

vi.mock("../../src/api/client", () => ({
  diagnoseCodeFileContent: vi.fn(),
  getCodeFileContent: vi.fn(),
  getFilesIndex: vi.fn(),
  getSourceImageUrl: vi.fn((taskId: string, filename: string): string =>
    `/api/v1/tasks/${taskId}/source-images/${filename}`,
  ),
  // #93：代码版面放大镜 sidecar；默认无（404→undefined），不影响既有断言。
  getTaskCodeLayout: vi.fn(() => Promise.resolve()),
  updateCodeFileContent: vi.fn(),
}));

const diagnoseCodeFileContentMock = vi.mocked(diagnoseCodeFileContent);
const getFilesIndexMock = vi.mocked(getFilesIndex);
const getCodeFileContentMock = vi.mocked(getCodeFileContent);
const updateCodeFileContentMock = vi.mocked(updateCodeFileContent);
const getTaskCodeLayoutMock = vi.mocked(getTaskCodeLayout);

function renderViewer(
  index: FilesIndex,
  content?: string,
  allSourceImages: string[] = ["raw/page1.JPG", "raw/page2.JPG"],
): void {
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
  diagnoseCodeFileContentMock.mockResolvedValue({
    path: "src/foo.cc",
    language: "cpp",
    status: "syntax_clean",
    category: "syntax",
    summary: "",
    failing_lines: [],
    syntax_errors: 0,
    semantic_errors: 0,
    dependency_errors: 0,
    items: [],
    tool: "g++",
    duration_ms: 1,
  });

  render(
    <LanguageProvider>
      <CodeViewer
        taskId="task-1"
        allSourceImages={allSourceImages}
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
    globalThis.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("源图缩略图按来源页解析出正确原图（data-page 落在 .source-image-cell）", async () => {
    renderViewer([
      {
        path: "src/foo.cc",
        filename: "foo.cc",
        language: "cpp",
        source_pages: ["page1.col0", "page2.col0"],
        source_page_ranges: [
          { page: "page1.col0", start_line: 1, end_line: 3 },
          { page: "page2.col0", start_line: 4, end_line: 5 },
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

    // 底部缩略图条的源图单元（.source-image-cell）按 page_stem 反查出正确原图，
    // data-page 落在单元上、原图为其内层 <img>。代码正文侧 data-page 锚点已随
    // scroll-sync 撤除而移除（仅源图侧保留 data-page，见设计 §11.1 D 项）。
    const imageAnchor = getRequiredElement(
      '.code-source-thumbs-list [data-page="page2.JPG"]',
    );
    expect(imageAnchor.querySelector("img")?.getAttribute("alt")).toBe(
      "raw/page2.JPG",
    );
  });

  it("含点 stem 的来源页仍解析出原图（剥尾 .colN，非按首个点切）", async () => {
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: ["a.b.col0"],
          source_page_ranges: [
            { page: "a.b.col0", start_line: 1, end_line: 5 },
          ],
          line_count: 5,
          line_no_range: [1, 5],
          flags: [],
        },
      ],
      undefined,
      ["raw/a.b.JPG"],
    );

    await screen.findByText("src/foo.cc");
    await waitFor(() => {
      expect(document.querySelector(".code-content-text")).not.toBeNull();
    });

    // page key "a.b.col0" 的 stem 为 "a.b"（剥掉结尾 .col0），匹配含点原图 raw/a.b.JPG。
    // 旧实现按 indexOf(".") 截成 "a" → 反查不到、该页源图缩略图缺失。断言从含点输入派生。
    const cell = getRequiredElement(
      '.code-source-thumbs-list [data-page="a.b.JPG"]',
    );
    expect(cell.querySelector("img")?.getAttribute("alt")).toBe("raw/a.b.JPG");
  });

  it("无来源页行号范围时仍按来源页渲染全部源图缩略图", async () => {
    renderViewer([
      {
        path: "src/foo.cc",
        filename: "foo.cc",
        language: "cpp",
        source_pages: ["page1.col0", "page2.col0"],
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

    // 缩略图由 source_pages 解析（不依赖 source_page_ranges）→ 两来源页两张缩略图。
    expect(
      document.querySelectorAll(
        ".code-source-thumbs-list .source-image-cell",
      ),
    ).toHaveLength(2);
  });

  it("渲染行号、语言着色和编译失败行", async () => {
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: ["page1.col0"],
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
          source_pages: ["page1.col0"],
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

  it("使用 diagnostic.items 渲染多处语法红色波浪线", async () => {
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: ["page1.col0"],
          source_page_ranges: [],
          line_count: 5,
          line_no_range: [20, 24],
          flags: [],
          compile_status: "failed",
          diagnostic: {
            path: "src/foo.cc",
            language: "cpp",
            status: "syntax_dirty",
            category: "syntax",
            summary: "multiple syntax errors",
            failing_lines: [21, 24],
            syntax_errors: 2,
            semantic_errors: 0,
            dependency_errors: 0,
            items: [
              {
                line: 21,
                column: 3,
                severity: "error",
                category: "syntax",
                code: "syntax_error",
                message: "expected ';'",
                source: "g++",
              },
              {
                line: 24,
                column: 3,
                severity: "error",
                category: "syntax",
                code: "syntax_error",
                message: "expected expression",
                source: "g++",
              },
            ],
            tool: "g++",
            duration_ms: 1,
          },
        },
      ],
      [
        "int first() {",
        "  BAD_ONE",
        "}",
        "int second() {",
        "  BAD_TWO",
      ].join("\n"),
    );

    await screen.findByText("src/foo.cc");

    const firstLine = await waitFor(() =>
      getRequiredElement('.code-line[data-line="21"]'),
    );
    const secondLine = getRequiredElement('.code-line[data-line="24"]');

    expect(firstLine.className).toContain("has-syntax-diagnostic");
    expect(secondLine.className).toContain("has-syntax-diagnostic");
    expect(firstLine.getAttribute("title")).toContain("expected ';'");
    expect(secondLine.getAttribute("title")).toContain("expected expression");
  });

  it("编辑态实时诊断并支持接受单条诊断", async () => {
    const user = userEvent.setup();
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: [],
          source_page_ranges: [],
          line_count: 2,
          line_no_range: [1, 2],
          flags: [],
        },
      ],
      '#include "missing.h"\nint main() { return 0; }',
    );
    diagnoseCodeFileContentMock.mockResolvedValue({
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
    });

    await screen.findByText("src/foo.cc");
    await user.click(screen.getByRole("button", { name: "编辑" }));

    await waitFor(() => {
      expect(diagnoseCodeFileContentMock).toHaveBeenCalledWith(
        "task-1",
        "src/foo.cc",
        '#include "missing.h"\nint main() { return 0; }',
      );
    });

    expect(
      await screen.findByText("dependency: missing.h: No such file or directory"),
    ).toBeTruthy();
    expect(
      getRequiredElement(".code-editor-edit-gutter .code-line-number")
        .className,
    ).toContain(
      "has-dependency-diagnostic",
    );

    await user.click(
      screen.getByRole("button", { name: "接受此诊断" }),
    );

    await waitFor(() => {
      expect(
        screen.queryByText("dependency: missing.h: No such file or directory"),
      ).toBeNull();
    });
    expect(
      getRequiredElement(".code-editor-edit-gutter .code-line-number")
        .className,
    ).not.toContain(
      "has-dependency-diagnostic",
    );
  });

  it("接受 include 依赖问题后仍显示后续语法诊断", async () => {
    const user = userEvent.setup();
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: [],
          source_page_ranges: [],
          line_count: 3,
          line_no_range: [1, 3],
          flags: [],
        },
      ],
      '#include "missing.h"\nint ok = 1;\nif(hEglImage 二 EGL_NO_IMAGE_KHR){ 王',
    );
    diagnoseCodeFileContentMock.mockResolvedValue({
      path: "src/foo.cc",
      language: "cpp",
      status: "syntax_dirty",
      category: "syntax",
      summary: "include and ocr noise",
      failing_lines: [1, 3],
      syntax_errors: 1,
      semantic_errors: 0,
      dependency_errors: 1,
      items: [
        {
          line: 1,
          column: 10,
          severity: "warn",
          category: "dependency",
          code: "missing_include",
          message: "missing.h: No such file or directory",
          source: "g++",
        },
        {
          line: 3,
          column: 14,
          severity: "error",
          category: "syntax",
          code: "ocr_noise_non_ascii",
          message: "OCR noise character '二' appears in code",
          source: "ocr-noise-scan",
        },
      ],
      tool: "g++",
      duration_ms: 1,
    });

    await screen.findByText("src/foo.cc");
    await user.click(screen.getByRole("button", { name: "编辑" }));

    expect(
      await screen.findByText("dependency: missing.h: No such file or directory"),
    ).toBeTruthy();
    expect(
      await screen.findByText("syntax: OCR noise character '二' appears in code"),
    ).toBeTruthy();

    const acceptButtons = screen.getAllByRole(
      "button",
      { name: "接受此诊断" },
    );
    const firstAcceptButton = acceptButtons[0];
    if (firstAcceptButton === undefined) {
      throw new Error("未找到接受诊断按钮");
    }
    await user.click(firstAcceptButton);

    await waitFor(() => {
      expect(
        screen.queryByText("dependency: missing.h: No such file or directory"),
      ).toBeNull();
    });
    expect(
      screen.getByText("syntax: OCR noise character '二' appears in code"),
    ).toBeTruthy();
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

  it("超大文件只渲染可视窗口的行（行级虚拟化）", async () => {
    const lineCount = 2000;
    const bigContent = Array.from(
      { length: lineCount },
      (_, i) => `int v${i.toString()} = ${i.toString()};`,
    ).join("\n");

    renderViewer(
      [
        {
          path: "src/big.cc",
          filename: "big.cc",
          language: "cpp",
          source_pages: ["page1.col0"],
          source_page_ranges: [
            { page: "page1.col0", start_line: 1, end_line: lineCount },
          ],
          line_count: lineCount,
          line_no_range: [1, lineCount],
          flags: [],
        },
      ],
      bigContent,
    );

    await screen.findByText("src/big.cc");
    await waitFor(() => {
      expect(document.querySelector(".code-content-text")).not.toBeNull();
    });

    // 虚拟化：DOM 中实际渲染的 .code-line 远少于总行数 2000。
    await waitFor(() => {
      const rendered = document.querySelectorAll(".code-line").length;
      expect(rendered).toBeGreaterThan(0);
      expect(rendered).toBeLessThan(100);
    });

    // 下方 spacer 用大高度占位，保证滚动条比例与总行数一致。
    const spacers = [
      ...document.querySelectorAll<HTMLElement>(".code-virtual-spacer"),
    ];
    const maxSpacer = Math.max(
      ...spacers.map((el) => Number.parseFloat(el.style.height) || 0),
    );
    expect(maxSpacer).toBeGreaterThan(1000);
  });

  it("编辑态移动光标 → 高亮当前行号并定位该行所属源图缩略图", async () => {
    const user = userEvent.setup();
    // sidecar：3 行 bbox 均属 page1（行号即 OCR line_no，与 data-line / gutter 同源）。
    getTaskCodeLayoutMock.mockResolvedValue({
      processed: false,
      files: [
        {
          path: "src/foo.cc",
          lines: [
            { line_no: 1, page: "page1.col0", bbox: [0, 0, 10, 10] },
            { line_no: 2, page: "page1.col0", bbox: [0, 10, 10, 20] },
            { line_no: 3, page: "page1.col0", bbox: [0, 20, 10, 30] },
          ],
          line_map: [],
        },
      ],
    });
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: ["page1.col0"],
          source_page_ranges: [
            { page: "page1.col0", start_line: 1, end_line: 3 },
          ],
          line_count: 3,
          line_no_range: [1, 3],
          flags: [],
        },
      ],
      "a\nb\nc",
    );

    await screen.findByText("src/foo.cc");
    await user.click(screen.getByRole("button", { name: "编辑" }));

    const textarea = screen.getByLabelText<HTMLTextAreaElement>(
      "编辑代码文件内容",
    );
    // 光标置于第 2 行（偏移 2 = "a\n" 之后），keyUp 上报活动行。
    textarea.setSelectionRange(2, 2);
    fireEvent.keyUp(textarea);

    // gutter 当前行高亮落在行号 "2"；该行 bbox 属 page1 → 对应缩略图描边 active。
    await waitFor(() => {
      const current = document.querySelector(
        ".code-editor-edit-gutter .code-line-number.current-line",
      );
      expect(current?.textContent).toBe("2");
      expect(
        document.querySelector(
          ".code-source-thumbs-list .source-image-cell.active",
        ),
      ).not.toBeNull();
    });

    // 光标移到第 3 行（偏移 4 = "a\nb\n" 之后）→ 高亮跟随到 "3"。
    textarea.setSelectionRange(4, 4);
    fireEvent.keyUp(textarea);
    await waitFor(() => {
      expect(
        document.querySelector(
          ".code-editor-edit-gutter .code-line-number.current-line",
        )?.textContent,
      ).toBe("3");
    });

    // forward 多行拖选（0→4）：光标在焦点端 selectionEnd=4 → 跟随到末行 "3"。
    textarea.setSelectionRange(0, 4, "forward");
    fireEvent.keyUp(textarea);
    await waitFor(() => {
      expect(
        document.querySelector(
          ".code-editor-edit-gutter .code-line-number.current-line",
        )?.textContent,
      ).toBe("3");
    });
    // backward 拖选（0→4）：光标在 selectionStart=0 → 跟随到首行 "1"。
    textarea.setSelectionRange(0, 4, "backward");
    fireEvent.keyUp(textarea);
    await waitFor(() => {
      expect(
        document.querySelector(
          ".code-editor-edit-gutter .code-line-number.current-line",
        )?.textContent,
      ).toBe("1");
    });
  });

  it("切换到编辑态清空只读悬停的活动行，首次落光标后才高亮（无幽灵当前行）", async () => {
    const user = userEvent.setup();
    getTaskCodeLayoutMock.mockResolvedValue({
      processed: false,
      files: [
        {
          path: "src/foo.cc",
          lines: [
            { line_no: 1, page: "page1.col0", bbox: [0, 0, 10, 10] },
            { line_no: 2, page: "page1.col0", bbox: [0, 10, 10, 20] },
            { line_no: 3, page: "page1.col0", bbox: [0, 20, 10, 30] },
          ],
          line_map: [],
        },
      ],
    });
    renderViewer(
      [
        {
          path: "src/foo.cc",
          filename: "foo.cc",
          language: "cpp",
          source_pages: ["page1.col0"],
          source_page_ranges: [
            { page: "page1.col0", start_line: 1, end_line: 3 },
          ],
          line_count: 3,
          line_no_range: [1, 3],
          flags: [],
        },
      ],
      "a\nb\nc",
    );

    await screen.findByText("src/foo.cc");
    await waitFor(() => {
      expect(document.querySelector(".code-content-text")).not.toBeNull();
    });

    // 只读态悬停第 2 行 → 设活动行=2。
    fireEvent.mouseMove(getRequiredElement('.code-content-text [data-line="2"]'));
    await user.click(screen.getByRole("button", { name: "编辑" }));

    // 进编辑态后 reset 生效：尚无 current-line（不把悬停行当幽灵当前行）。
    await waitFor(() => {
      expect(document.querySelector(".code-editor-edit-gutter")).not.toBeNull();
    });
    expect(
      document.querySelector(
        ".code-editor-edit-gutter .code-line-number.current-line",
      ),
    ).toBeNull();

    // 落光标到第 1 行 → 高亮才出现在 "1"。
    const textarea = screen.getByLabelText<HTMLTextAreaElement>(
      "编辑代码文件内容",
    );
    textarea.setSelectionRange(0, 0);
    fireEvent.keyUp(textarea);
    await waitFor(() => {
      expect(
        document.querySelector(
          ".code-editor-edit-gutter .code-line-number.current-line",
        )?.textContent,
      ).toBe("1");
    });
  });
});
