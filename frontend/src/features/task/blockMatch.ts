/**
 * 光标块 ↔ 版面块模糊匹配（Epic E · E3，设计 §8）。
 *
 * 编辑器里光标所在块的纯文本（已被 LLM 精修改写）与某页候选块的 raw OCR 文字
 * 做模糊匹配，命中得分最高者用于高亮其 bbox。同页候选集很小（5~15 块），
 * 归一化后按子串/最长公共子串比例评分，低于阈值返回 undefined（不高亮优于错高亮）。
 */

import type { LayoutBlockPayload } from "../../api/schemas";

/** 命中阈值：最高分低于此值视为未命中（退化为不高亮）。 */
const MATCH_THRESHOLD = 0.5;
/** 归一化截断长度：标题/短段足够区分，长段取前缀即可。 */
const PREFIX_LEN = 40;

/**
 * 归一化：转小写 → 去全部空白 + 标点 + 符号 → 截前 N 字。
 *
 * 精修可能改动空白/标点/大小写，但正文字符大体保留，去噪后比对更鲁棒。
 */
export function normalizeForMatch(text: string): string {
  return text
    .toLowerCase()
    .replaceAll(/[\s\p{P}\p{S}]/gu, "")
    .slice(0, PREFIX_LEN);
}

/** 最长公共子串长度（O(n·m) 滚动数组；归一化后串 ≤ PREFIX_LEN，开销可忽略）。 */
function longestCommonSubstring(a: string, b: string): number {
  const n = a.length;
  const m = b.length;
  if (n === 0 || m === 0) return 0;
  let best = 0;
  let prev = Array.from<number>({ length: m + 1 }).fill(0);
  for (let i = 1; i <= n; i += 1) {
    const curr = Array.from<number>({ length: m + 1 }).fill(0);
    for (let j = 1; j <= m; j += 1) {
      if (a[i - 1] === b[j - 1]) {
        const val = (prev[j - 1] ?? 0) + 1;
        curr[j] = val;
        if (val > best) best = val;
      }
    }
    prev = curr;
  }
  return best;
}

/**
 * 两归一化串的重合度 [0,1]：一者为另一者子串 → 1；否则最长公共子串 / 较短串长。
 */
function overlapScore(a: string, b: string): number {
  if (a === "" || b === "") return 0;
  const shorter = a.length <= b.length ? a : b;
  const longer = a.length <= b.length ? b : a;
  if (longer.includes(shorter)) return 1;
  return longestCommonSubstring(a, b) / shorter.length;
}

/**
 * 在候选块里选与 ``cursorText`` 重合度最高者；最高分 < 阈值 → undefined。
 *
 * 平手时保留先遇到的（阅读序靠前），稳定不抖动。
 */
export function matchBlock(
  blocks: readonly LayoutBlockPayload[],
  cursorText: string,
): LayoutBlockPayload | undefined {
  const target = normalizeForMatch(cursorText);
  if (target === "") return undefined;
  let best: LayoutBlockPayload | undefined;
  let bestScore = 0;
  for (const block of blocks) {
    const candidate = normalizeForMatch(block.text);
    if (candidate === "") continue;
    const score = overlapScore(target, candidate);
    if (score > bestScore) {
      bestScore = score;
      best = block;
    }
  }
  return bestScore >= MATCH_THRESHOLD ? best : undefined;
}
