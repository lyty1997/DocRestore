import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getNerSetupStatus,
  getNerStatus,
  getOcrStatus,
  listGpus,
  startNerSetup,
  warmupOcrEngine,
} from "../../src/api/client";
import type {
  NerSetupStatusResponse,
  NerStatusResponse,
} from "../../src/api/schemas";
import { TaskForm } from "../../src/components/TaskForm";
import { LanguageProvider } from "../../src/i18n";

/* 保留真实 ApiError（handleNerSetup 用 instanceof 判 409），仅打桩网络函数。 */
vi.mock("../../src/api/client", async (importActual) => {
  const actual = await importActual<typeof import("../../src/api/client")>();
  return {
    ...actual,
    getOcrStatus: vi.fn(),
    listGpus: vi.fn(),
    warmupOcrEngine: vi.fn(),
    getNerStatus: vi.fn(),
    startNerSetup: vi.fn(),
    getNerSetupStatus: vi.fn(),
  };
});

vi.mock("../../src/components/SourcePicker", () => ({
  SourcePicker: ({
    onComplete,
  }: {
    readonly onComplete: (imageDir: string) => void;
  }): React.JSX.Element => (
    <button
      type="button"
      onClick={() => {
        onComplete("/tmp/imgs");
      }}
    >
      mock source
    </button>
  ),
}));

vi.mock("../../src/components/DirectoryPicker", () => ({
  DirectoryPicker: (): React.JSX.Element => <div>mock dir</div>,
}));

const listGpusMock = vi.mocked(listGpus);
const getOcrStatusMock = vi.mocked(getOcrStatus);
const warmupOcrEngineMock = vi.mocked(warmupOcrEngine);
const getNerStatusMock = vi.mocked(getNerStatus);
const startNerSetupMock = vi.mocked(startNerSetup);
const getNerSetupStatusMock = vi.mocked(getNerSetupStatus);

type User = ReturnType<typeof userEvent.setup>;

const UNAVAILABLE: NerStatusResponse = {
  available: false,
  spacy_installed: false,
  configured_models: ["zh_core_web_md", "en_core_web_md"],
  installed_models: [],
  missing_models: ["zh_core_web_md", "en_core_web_md"],
};
const AVAILABLE: NerStatusResponse = {
  available: true,
  spacy_installed: true,
  configured_models: ["zh_core_web_md", "en_core_web_md"],
  installed_models: ["zh_core_web_md", "en_core_web_md"],
  missing_models: [],
};

function setupStatus(
  state: NerSetupStatusResponse["state"],
  extra: Partial<NerSetupStatusResponse> = {},
): NerSetupStatusResponse {
  return { state, log: [], error: "", ...extra };
}

function renderForm(onSubmit = vi.fn()): void {
  render(
    <LanguageProvider>
      <TaskForm onSubmit={onSubmit} disabled={false} />
    </LanguageProvider>,
  );
}

/** 用 instanceof 收窄到 HTMLButtonElement，避免 as 断言。 */
function getSubmitButton(): HTMLButtonElement {
  const el = screen.getByRole("button", { name: "开始处理" });
  if (!(el instanceof HTMLButtonElement)) {
    throw new TypeError("提交按钮类型异常");
  }
  return el;
}

async function enablePii(user: User): Promise<void> {
  const toggle = document.querySelector<HTMLInputElement>("#pii-toggle");
  if (toggle === null) throw new Error("找不到脱敏开关");
  await user.click(toggle);
}

describe("TaskForm 本地 NER 一键配置", () => {
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

  it("开启 PII 且本地 NER 不可用 → 弹一键配置告警并禁止提交", async () => {
    getNerStatusMock.mockResolvedValue(UNAVAILABLE);
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole("button", { name: "mock source" }));
    await enablePii(user);

    await waitFor(() => {
      expect(document.querySelector(".ner-warning")).not.toBeNull();
    });
    expect(document.querySelector(".btn-ner-setup")).not.toBeNull();
    expect(getSubmitButton().disabled).toBe(true);
  });

  it("本地 NER 可用 → 无告警、可提交", async () => {
    getNerStatusMock.mockResolvedValue(AVAILABLE);
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole("button", { name: "mock source" }));
    await enablePii(user);

    await waitFor(() => {
      expect(document.querySelector(".ner-ready")).not.toBeNull();
    });
    expect(document.querySelector(".ner-warning")).toBeNull();
    expect(getSubmitButton().disabled).toBe(false);
  });

  it("点一键配置 → 安装完成后告警消失、放行提交", async () => {
    /* 首次探测不可用；安装 done 后复检可用 */
    getNerStatusMock.mockResolvedValueOnce(UNAVAILABLE);
    getNerStatusMock.mockResolvedValue(AVAILABLE);
    startNerSetupMock.mockResolvedValue(setupStatus("running"));
    getNerSetupStatusMock.mockResolvedValue(setupStatus("done"));
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole("button", { name: "mock source" }));
    await enablePii(user);

    const setupBtn = await screen.findByRole("button", {
      name: "一键配置本地 NER 环境",
    });
    await user.click(setupBtn);
    expect(startNerSetupMock).toHaveBeenCalledTimes(1);

    /* 轮询（1500ms）拿到 done → refreshNerStatus → available → 告警消失 */
    await waitFor(
      () => {
        expect(document.querySelector(".ner-warning")).toBeNull();
      },
      { timeout: 4000 },
    );
    expect(getSubmitButton().disabled).toBe(false);
  });

  it("安装失败 → 显示错误与重试入口", async () => {
    getNerStatusMock.mockResolvedValue(UNAVAILABLE);
    startNerSetupMock.mockResolvedValue(setupStatus("running"));
    getNerSetupStatusMock.mockResolvedValue(
      setupStatus("failed", { error: "下载语言模型失败" }),
    );
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByRole("button", { name: "mock source" }));
    await enablePii(user);

    const setupBtn = await screen.findByRole("button", {
      name: "一键配置本地 NER 环境",
    });
    await user.click(setupBtn);

    await waitFor(
      () => {
        expect(document.querySelector(".ner-setup-error")).not.toBeNull();
      },
      { timeout: 4000 },
    );
    /* 失败后按钮文案切换为"重试配置"，仍禁止提交 */
    expect(
      screen.getByRole("button", { name: "重试配置" }),
    ).not.toBeNull();
    expect(getSubmitButton().disabled).toBe(true);
  });
});
