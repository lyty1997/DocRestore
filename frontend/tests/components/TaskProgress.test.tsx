/**
 * TaskProgress 组件测试
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TaskProgress } from "../../src/components/TaskProgress";
import { LanguageProvider } from "../../src/i18n";

function renderProgress(props: React.ComponentProps<typeof TaskProgress>): void {
  render(
    <LanguageProvider>
      <TaskProgress {...props} />
    </LanguageProvider>,
  );
}

describe("TaskProgress", () => {
  it("无 progress 时显示 '等待开始'", () => {
    renderProgress({
      taskId: "abc",
      progress: undefined,
      wsState: "closed",
      pollingEnabled: false,
    });
    expect(screen.getByText("等待开始")).toBeInTheDocument();
    expect(screen.getByText(/abc/)).toBeInTheDocument();
  });

  it("有 progress 时显示阶段标签 + 百分比", () => {
    renderProgress({
      taskId: "t1",
      progress: {
        stage: "ocr",
        current: 3,
        total: 10,
        percent: 30,
        message: "识别中",
      },
      wsState: "open",
      pollingEnabled: false,
    });
    expect(screen.getByText("OCR 识别")).toBeInTheDocument();
    expect(screen.getByText("3/10 (30.0%)")).toBeInTheDocument();
    expect(screen.getByText("识别中")).toBeInTheDocument();
    expect(screen.getByText("WS")).toBeInTheDocument();
  });

  it("未知 stage 时回落显示原始 stage 字符串", () => {
    renderProgress({
      taskId: "t1",
      progress: {
        stage: "未注册阶段",
        current: 0,
        total: 0,
        percent: 0,
        message: "",
      },
      wsState: "closed",
      pollingEnabled: false,
    });
    expect(screen.getByText("未注册阶段")).toBeInTheDocument();
  });

  it("pollingEnabled=true 且 ws 非 open 时显示 '轮询'", () => {
    renderProgress({
      taskId: "t1",
      progress: undefined,
      wsState: "closed",
      pollingEnabled: true,
    });
    expect(screen.getByText("轮询")).toBeInTheDocument();
  });

  it("无 taskId 时显示占位符 '—'", () => {
    renderProgress({
      taskId: undefined,
      progress: undefined,
      wsState: "closed",
      pollingEnabled: false,
    });
    expect(screen.getByText("任务：—")).toBeInTheDocument();
  });
});
