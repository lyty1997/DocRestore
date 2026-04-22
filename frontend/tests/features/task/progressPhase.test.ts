/**
 * 进度阶段分轨单元测试
 */

import { describe, expect, it } from "vitest";

import type { TaskProgress } from "../../../src/api/schemas";
import {
  mergeProgressFrame,
  phaseOfStage,
  type ProgressBuckets,
} from "../../../src/features/task/progressPhase";

function frame(
  overrides: Partial<TaskProgress> & { stage: string },
): TaskProgress {
  return {
    stage: overrides.stage,
    current: overrides.current ?? 0,
    total: overrides.total ?? 0,
    percent: overrides.percent ?? 0,
    message: overrides.message ?? "",
    subtask: overrides.subtask ?? "",
  };
}

describe("phaseOfStage", () => {
  it("ocr 与 init 归入 ocr 轨", () => {
    expect(phaseOfStage("ocr")).toBe("ocr");
    expect(phaseOfStage("init")).toBe("ocr");
  });

  it("refine 与其余未知 stage 归入 llm 轨", () => {
    expect(phaseOfStage("refine")).toBe("llm");
    expect(phaseOfStage("render")).toBe("llm");
    expect(phaseOfStage("doc_boundary")).toBe("llm");
    expect(phaseOfStage("final_refine")).toBe("llm");
    expect(phaseOfStage("unknown_stage")).toBe("llm");
  });
});

describe("mergeProgressFrame", () => {
  it("在同一 subtask 下同时保留 OCR 与 LLM 帧", () => {
    let buckets: ProgressBuckets = {};
    buckets = mergeProgressFrame(
      buckets,
      frame({ stage: "ocr", current: 3, total: 7, subtask: "" }),
    );
    buckets = mergeProgressFrame(
      buckets,
      frame({ stage: "refine", current: 2, total: 0, subtask: "" }),
    );
    expect(buckets[""]?.ocr?.current).toBe(3);
    expect(buckets[""]?.llm?.current).toBe(2);
  });

  it("新 OCR 帧覆盖旧 OCR 帧，但不影响 LLM 桶", () => {
    let buckets: ProgressBuckets = {};
    buckets = mergeProgressFrame(
      buckets,
      frame({ stage: "ocr", current: 1, total: 7 }),
    );
    buckets = mergeProgressFrame(
      buckets,
      frame({ stage: "refine", current: 1, total: 0 }),
    );
    buckets = mergeProgressFrame(
      buckets,
      frame({ stage: "ocr", current: 5, total: 7 }),
    );
    expect(buckets[""]?.ocr?.current).toBe(5);
    expect(buckets[""]?.llm?.current).toBe(1);
  });

  it("不同 subtask 的帧彼此隔离", () => {
    let buckets: ProgressBuckets = {};
    buckets = mergeProgressFrame(
      buckets,
      frame({ stage: "ocr", current: 2, total: 4, subtask: "doc-a" }),
    );
    buckets = mergeProgressFrame(
      buckets,
      frame({ stage: "refine", current: 1, total: 0, subtask: "doc-b" }),
    );
    expect(buckets["doc-a"]?.ocr?.current).toBe(2);
    expect(buckets["doc-a"]?.llm).toBeUndefined();
    expect(buckets["doc-b"]?.ocr).toBeUndefined();
    expect(buckets["doc-b"]?.llm?.current).toBe(1);
  });
});
