/**
 * TaskProgress 第二轨（LLM 精修 / 后处理）展示规则测试。
 *
 * 规则：关精修时，文档模式隐藏第二轨（纯噪声），PPT/代码模式保留但改名「后处理」。
 * 断言走 CSS 类（.phase-row-llm / .phase-label）而非 stage 文案，避免与
 * stageLabels.refine="LLM 精修" 文本撞车。默认语言 zh-CN（同其它组件测试）。
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { TaskProgress as TaskProgressData } from "../../src/api/schemas";
import { TaskProgress } from "../../src/components/TaskProgress";
import type { ProgressBuckets } from "../../src/features/task/progressPhase";
import { LanguageProvider } from "../../src/i18n";

afterEach(cleanup);

function frame(stage: string): TaskProgressData {
  return {
    stage,
    current: 1,
    total: 3,
    percent: 33,
    message: "",
    subtask: "",
    message_key: "",
    message_params: {},
  };
}

/* 主桶非空（单目录任务）才渲染第二轨；llm 帧用 render 避免 stage 文案撞「精修」 */
const BUCKETS: ProgressBuckets = {
  "": { ocr: frame("ocr"), llm: frame("render") },
};

function renderProgress(props: {
  refineEnabled?: boolean;
  mode?: "doc" | "code" | "ppt";
}): HTMLElement {
  const { container } = render(
    <LanguageProvider>
      <TaskProgress
        taskId="t1"
        progresses={BUCKETS}
        wsState="open"
        pollingEnabled={false}
        {...props}
      />
    </LanguageProvider>,
  );
  return container;
}

function llmPhaseLabel(c: HTMLElement): string | undefined {
  return (
    c.querySelector(".phase-row-llm .phase-label")?.textContent ?? undefined
  );
}

describe("TaskProgress 第二轨展示", () => {
  it("精修开 + 文档模式：显示第二轨，标签「LLM 精修」", () => {
    const c = renderProgress({ refineEnabled: true, mode: "doc" });
    expect(c.querySelector(".phase-row-llm")).not.toBeNull();
    expect(llmPhaseLabel(c)).toBe("LLM 精修");
  });

  it("缺省（未传 props）：按精修开 + 文档处理，显示「LLM 精修」", () => {
    const c = renderProgress({});
    expect(llmPhaseLabel(c)).toBe("LLM 精修");
  });

  it("精修关 + 文档模式：隐藏第二轨，仅保留 OCR 轨", () => {
    const c = renderProgress({ refineEnabled: false, mode: "doc" });
    expect(c.querySelector(".phase-row-llm")).toBeNull();
    expect(c.querySelector(".phase-row-ocr")).not.toBeNull();
  });

  it("精修关 + PPT 模式：保留第二轨，标签改「后处理」", () => {
    const c = renderProgress({ refineEnabled: false, mode: "ppt" });
    expect(c.querySelector(".phase-row-llm")).not.toBeNull();
    expect(llmPhaseLabel(c)).toBe("后处理");
  });

  it("精修关 + 代码模式：保留第二轨，标签改「后处理」", () => {
    const c = renderProgress({ refineEnabled: false, mode: "code" });
    expect(c.querySelector(".phase-row-llm")).not.toBeNull();
    expect(llmPhaseLabel(c)).toBe("后处理");
  });
});
