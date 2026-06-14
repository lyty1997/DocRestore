import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getOcrStatus,
  listGpus,
  warmupOcrEngine,
} from "../../src/api/client";
import { TaskForm } from "../../src/components/TaskForm";
import { LanguageProvider } from "../../src/i18n";

vi.mock("../../src/api/client", () => ({
  getOcrStatus: vi.fn(),
  listGpus: vi.fn(),
  warmupOcrEngine: vi.fn(),
  getNerStatus: vi.fn(),
  startNerSetup: vi.fn(),
  getNerSetupStatus: vi.fn(),
}));

vi.mock("../../src/components/SourcePicker", () => ({
  SourcePicker: ({
    onComplete,
  }: {
    readonly onComplete: (imageDir: string) => void;
  }): React.JSX.Element => (
    <button
      type="button"
      onClick={() => {
        onComplete("/tmp/code-images");
      }}
    >
      mock source
    </button>
  ),
}));

vi.mock("../../src/components/DirectoryPicker", () => ({
  DirectoryPicker: (): React.JSX.Element => <div>mock directory picker</div>,
}));

const listGpusMock = vi.mocked(listGpus);
const getOcrStatusMock = vi.mocked(getOcrStatus);
const warmupOcrEngineMock = vi.mocked(warmupOcrEngine);

describe("TaskForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listGpusMock.mockResolvedValue({ gpus: [], recommended: undefined });
    getOcrStatusMock.mockRejectedValue(new Error("offline"));
    warmupOcrEngineMock.mockResolvedValue({ status: "ready", message: "" });
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("代码模式默认 PaddleOCR 时提交 basic pipeline", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <LanguageProvider>
        <TaskForm onSubmit={onSubmit} disabled={false} />
      </LanguageProvider>,
    );

    await user.click(screen.getByRole("button", { name: "mock source" }));
    const toggle = document.querySelector<HTMLInputElement>("#mode-code");
    if (toggle === null) {
      throw new Error("找不到代码模式开关");
    }
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "开始处理" }));

    expect(onSubmit).toHaveBeenCalledWith(
      "/tmp/code-images",
      undefined,
      undefined,
      undefined,
      {
        model: "paddle-ocr/ppocr-v4",
        gpu_id: undefined,
        paddle_pipeline: "basic",
      },
      { enable: true },
      undefined,
      undefined,
    );
  });
});
