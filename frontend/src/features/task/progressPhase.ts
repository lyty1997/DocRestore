/**
 * 进度帧按阶段分轨工具。
 *
 * 流式 Pipeline 并发发 OCR 帧与 LLM 精修帧：
 *   - OCR Producer: stage ∈ {init, ocr}
 *   - Stream Processor / 终结化: stage ∈ {clean, merge, refine, doc_boundary,
 *     final_refine, render}
 *
 * 前端按 (subtask, phase) 分桶，每个子目录同时展示 OCR/LLM 两条进度条。
 */

import type { TaskProgress } from "../../api/schemas";

/** 进度阶段类型 */
export type ProgressPhase = "ocr" | "llm";

/** 单子目录的进度桶：OCR 与 LLM 两轨，任一可缺失 */
export type SubtaskProgress = Partial<Record<ProgressPhase, TaskProgress>>;

/** 全量进度：key 为 subtask（空串为主进度/单目录），value 为分轨桶 */
export type ProgressBuckets = Record<string, SubtaskProgress>;

/** stage → phase 映射（未识别的 stage 默认归入 llm 轨，避免丢帧） */
export function phaseOfStage(stage: string): ProgressPhase {
  if (stage === "ocr" || stage === "init") return "ocr";
  return "llm";
}

/** 不可变合并：把一帧更新到 (subtask, phase) 桶中 */
export function mergeProgressFrame(
  prev: ProgressBuckets,
  frame: TaskProgress,
): ProgressBuckets {
  const phase = phaseOfStage(frame.stage);
  const existing = prev[frame.subtask] ?? {};
  return {
    ...prev,
    [frame.subtask]: { ...existing, [phase]: frame },
  };
}
