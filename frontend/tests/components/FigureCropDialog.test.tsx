import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { cropFigure, getSourceImageUrl, listSourceImages } from "../../src/api/client";
import { FigureCropDialog } from "../../src/components/FigureCropDialog";
import { LanguageProvider } from "../../src/i18n";

vi.mock("../../src/api/client", () => ({
  cropFigure: vi.fn(),
  getSourceImageUrl: vi.fn(),
  listSourceImages: vi.fn(),
}));

const listSourceImagesMock = vi.mocked(listSourceImages);
const getSourceImageUrlMock = vi.mocked(getSourceImageUrl);
const cropFigureMock = vi.mocked(cropFigure);

// jsdom 不提供真实 naturalWidth/Height：桩成固定值，让 onImgLoad 能拿到尺寸
beforeAll(() => {
  Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", {
    configurable: true,
    get: () => 600,
  });
  Object.defineProperty(HTMLImageElement.prototype, "naturalHeight", {
    configurable: true,
    get: () => 400,
  });
});

function renderDialog(
  onConfirm = vi.fn(),
  onClose = vi.fn(),
): { onConfirm: ReturnType<typeof vi.fn>; onClose: ReturnType<typeof vi.fn> } {
  render(
    <LanguageProvider>
      <FigureCropDialog taskId="t1" onConfirm={onConfirm} onClose={onClose} />
    </LanguageProvider>,
  );
  return { onConfirm, onClose };
}

describe("FigureCropDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listSourceImagesMock.mockResolvedValue({
      task_id: "t1",
      images: ["a.jpg", "b.jpg"],
    });
    getSourceImageUrlMock.mockImplementation((_t, name) => `blob:${name}`);
    cropFigureMock.mockResolvedValue({ asset_path: "images/manual_1.jpg" });
  });

  afterEach(cleanup);

  it("加载源图列表并填充下拉选项", async () => {
    renderDialog();
    const select = await screen.findByRole("combobox");
    const options = select.querySelectorAll("option");
    expect([...options].map((o) => o.value)).toEqual(["a.jpg", "b.jpg"]);
  });

  it("取消按钮触发 onClose", async () => {
    const { onClose } = renderDialog();
    await screen.findByRole("combobox");
    const user = userEvent.setup();
    // 页脚取消按钮（与右上角 × 区分：取文本）
    await user.click(screen.getByText("取消"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("光标所在页匹配源图时自动选中该页并提示", async () => {
    render(
      <LanguageProvider>
        <FigureCropDialog
          taskId="t1"
          cursorPage="b.jpg"
          onConfirm={vi.fn()}
          onClose={vi.fn()}
        />
      </LanguageProvider>,
    );
    await screen.findByRole("combobox");
    // 下拉当前显示值为光标所在页（findByDisplayValue 按选中项校验）
    expect(await screen.findByDisplayValue("b.jpg")).not.toBeNull();
    // 自动锚定提示出现
    expect(screen.getByText(/自动选中/)).not.toBeNull();
  });

  it("多文档任务按基名 + docDir 前缀锚定到对应子目录源图", async () => {
    listSourceImagesMock.mockResolvedValue({
      task_id: "t1",
      images: ["docA/p1.jpg", "docB/p1.jpg"],
    });
    render(
      <LanguageProvider>
        <FigureCropDialog
          taskId="t1"
          docDir="docB"
          cursorPage="p1.jpg"
          onConfirm={vi.fn()}
          onClose={vi.fn()}
        />
      </LanguageProvider>,
    );
    await screen.findByRole("combobox");
    expect(await screen.findByDisplayValue("docB/p1.jpg")).not.toBeNull();
  });

  it("光标页无匹配源图时回退列表首张且不提示", async () => {
    render(
      <LanguageProvider>
        <FigureCropDialog
          taskId="t1"
          cursorPage="missing.jpg"
          onConfirm={vi.fn()}
          onClose={vi.fn()}
        />
      </LanguageProvider>,
    );
    await screen.findByRole("combobox");
    expect(await screen.findByDisplayValue("a.jpg")).not.toBeNull();
    expect(screen.queryByText(/自动选中/)).toBeNull();
  });

  it("源图加载出尺寸后确认 → 调用 cropFigure 并回调 asset_path", async () => {
    const { onConfirm } = renderDialog();
    await screen.findByRole("combobox");
    // 触发测量图 load（jsdom 不会自动派发），让 onImgLoad 设置尺寸+初始框
    const [firstImg] = document.querySelectorAll("img");
    if (firstImg === undefined) throw new Error("测量图未渲染");
    fireEvent.load(firstImg);

    const user = userEvent.setup();
    const confirmBtn = await screen.findByText("裁剪并插入");
    // 无 jest-dom：用属性判断启用态（React disabled=false 时不渲染 disabled 属性）
    await waitFor(() => {
      expect(confirmBtn.hasAttribute("disabled")).toBe(false);
    });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(cropFigureMock).toHaveBeenCalledTimes(1);
    });
    expect(cropFigureMock).toHaveBeenCalledWith(
      "t1",
      expect.objectContaining({ source_filename: "a.jpg", doc_dir: undefined }),
    );
    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalledWith("images/manual_1.jpg");
    });
  });
});
