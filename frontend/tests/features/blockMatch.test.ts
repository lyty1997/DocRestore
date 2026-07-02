import { describe, expect, it } from "vitest";

import type { LayoutBlockPayload } from "../../src/api/schemas";
import {
  matchBlock,
  normalizeForMatch,
} from "../../src/features/task/blockMatch";

function block(text: string, label = "text"): LayoutBlockPayload {
  return { bbox: [0, 0, 10, 10], label, index: 0, text, image_ref: "" };
}

describe("normalizeForMatch", () => {
  it("去空白 + 标点 + 转小写", () => {
    expect(normalizeForMatch("Hello, World！ 你好。")).toBe("helloworld你好");
  });

  it("截前 40 字", () => {
    const long = "a".repeat(100);
    expect(normalizeForMatch(long)).toHaveLength(40);
  });
});

describe("matchBlock", () => {
  it("完全相同 → 命中该块", () => {
    const blocks = [block("第一章 绪论"), block("正文内容")];
    expect(matchBlock(blocks, "第一章 绪论")).toBe(blocks[0]);
  });

  it("精修改了标点/空白但正文一致 → 仍命中（子串关系得满分）", () => {
    const blocks = [block("本文研究了OCR还原"), block("参考文献")];
    // 光标文字被精修：加了标点空格，归一化后与候选互为子串
    expect(matchBlock(blocks, "本文研究了 OCR 还原。")).toBe(blocks[0]);
  });

  it("部分重合超过阈值 → 命中重合最高块", () => {
    const blocks = [
      block("摘要本文提出一种新的版面还原方法效果显著"),
      block("完全不相干的另一段文字内容啊"),
    ];
    // 光标文字是该段的精修截断版，重合度高
    expect(matchBlock(blocks, "本文提出一种新的版面还原方法")).toBe(blocks[0]);
  });

  it("无相近块（重合低于阈值）→ undefined（不高亮优于错高亮）", () => {
    const blocks = [block("天气晴朗"), block("股票上涨")];
    expect(matchBlock(blocks, "量子纠缠的数学表述")).toBeUndefined();
  });

  it("空光标文字 → undefined", () => {
    expect(matchBlock([block("任意")], "   ")).toBeUndefined();
  });

  it("空候选列表 → undefined", () => {
    expect(matchBlock([], "任意文字")).toBeUndefined();
  });

  it("跳过空文本候选（图片块）", () => {
    const img = block("", "image");
    const txt = block("正文命中");
    expect(matchBlock([img, txt], "正文命中")).toBe(txt);
  });

  it("平手时取先遇到的（阅读序靠前，稳定不抖）", () => {
    const a = block("重复段落");
    const b = block("重复段落");
    expect(matchBlock([a, b], "重复段落")).toBe(a);
  });
});
