/**
 * useTaskRunner hook 测试
 *
 * 覆盖：
 * - startTask → WS open → progress → 关闭 → fetchResult
 * - createTask 失败时进入 failed
 * - WS schema 校验失败时降级到轮询
 * - reset 清理状态
 *
 * 这里 mock 全局 WebSocket，由测试驱动各事件。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTaskRunner } from "../../../src/features/task/useTaskRunner";

vi.mock("../../../src/api/client", () => ({
  createTask: vi.fn(),
  getTask: vi.fn(),
  getTaskResults: vi.fn(),
  getWsProgressUrl: vi.fn(() => "ws://test/progress"),
}));

import {
  createTask,
  getTask,
  getTaskResults,
} from "../../../src/api/client";

const mocked = {
  createTask: vi.mocked(createTask),
  getTask: vi.mocked(getTask),
  getTaskResults: vi.mocked(getTaskResults),
};

/** 极简 WebSocket mock：只保留测试需要的事件 + readyState */
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;

  url: string;
  readyState = 0;
  // 简单事件总线
  private listeners: Record<string, ((event: unknown) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, fn: (event: unknown) => void): void {
    (this.listeners[type] ??= []).push(fn);
  }

  removeEventListener(type: string, fn: (event: unknown) => void): void {
    this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn);
  }

  close(): void {
    this.readyState = 3;
    this.dispatch("close", { code: 1000 });
  }

  /** 测试辅助方法 */
  triggerOpen(): void {
    this.readyState = 1;
    this.dispatch("open", {});
  }

  triggerMessage(data: string): void {
    this.dispatch("message", { data });
  }

  triggerError(): void {
    this.dispatch("error", {});
  }

  private dispatch(type: string, event: unknown): void {
    for (const fn of this.listeners[type] ?? []) fn(event);
  }
}

beforeEach(() => {
  MockWebSocket.instances.length = 0;
  for (const fn of Object.values(mocked)) fn.mockReset();
  globalThis.WebSocket = MockWebSocket as unknown as typeof globalThis.WebSocket;
});

afterEach(() => {
  vi.useRealTimers();
});

function lastWs(): MockWebSocket {
  const ws = MockWebSocket.instances.at(-1);
  if (!ws) throw new Error("no WebSocket instance");
  return ws;
}

describe("useTaskRunner 初始状态", () => {
  it("默认 idle", () => {
    const { result } = renderHook(() => useTaskRunner());
    expect(result.current.status).toBe("idle");
    expect(result.current.taskId).toBeUndefined();
    expect(result.current.wsState).toBe("closed");
    expect(result.current.allResults).toEqual([]);
  });
});

describe("useTaskRunner.startTask 成功路径", () => {
  it("WS 推送 progress → 关闭后拉取结果，进入 completed", async () => {
    mocked.createTask.mockResolvedValue({ task_id: "t1", status: "pending" });
    mocked.getTask.mockResolvedValue({ task_id: "t1", status: "completed" });
    mocked.getTaskResults.mockResolvedValue({
      task_id: "t1",
      results: [
        {
          task_id: "t1",
          output_path: "/o",
          markdown: "# done",
          doc_title: "标题",
          doc_dir: "doc1",
        },
      ],
    });

    const { result } = renderHook(() => useTaskRunner());
    act(() => {
      result.current.startTask("/tmp/imgs");
    });

    await waitFor(() => {
      expect(result.current.taskId).toBe("t1");
    });
    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => {
      lastWs().triggerOpen();
    });
    expect(result.current.wsState).toBe("open");

    act(() => {
      lastWs().triggerMessage(
        JSON.stringify({
          stage: "ocr",
          current: 1,
          total: 10,
          percent: 10,
          message: "ing",
        }),
      );
    });
    expect(result.current.progress?.stage).toBe("ocr");
    expect(result.current.status).toBe("processing");

    act(() => {
      lastWs().close();
    });

    await waitFor(() => {
      expect(result.current.status).toBe("completed");
    });
    expect(result.current.resultMarkdown).toBe("# done");
    expect(result.current.allResults).toHaveLength(1);
  });
});

describe("useTaskRunner.startTask 失败路径", () => {
  it("createTask 抛错时进入 failed 状态", async () => {
    mocked.createTask.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useTaskRunner());

    act(() => {
      result.current.startTask("/x");
    });

    await waitFor(() => {
      expect(result.current.status).toBe("failed");
    });
    expect(result.current.error).toBe("boom");
  });

  it("任务返回 failed 状态时记录 error", async () => {
    mocked.createTask.mockResolvedValue({ task_id: "t2", status: "pending" });
    mocked.getTask.mockResolvedValue({
      task_id: "t2",
      status: "failed",
      error: "OCR 错误",
    });

    const { result } = renderHook(() => useTaskRunner());
    act(() => {
      result.current.startTask("/x");
    });
    await waitFor(() => {
      expect(result.current.taskId).toBe("t2");
    });

    act(() => {
      lastWs().close();
    });

    await waitFor(() => {
      expect(result.current.status).toBe("failed");
    });
    expect(result.current.error).toBe("OCR 错误");
  });
});

describe("useTaskRunner WS 校验失败降级轮询", () => {
  it("无效 message 触发 close + polling", async () => {
    vi.useFakeTimers();
    mocked.createTask.mockResolvedValue({ task_id: "t3", status: "pending" });
    // close 后 REST 暂时未到终态 → 进入轮询
    mocked.getTask.mockResolvedValue({ task_id: "t3", status: "processing" });

    const { result } = renderHook(() => useTaskRunner());
    act(() => {
      result.current.startTask("/x");
    });
    await vi.waitFor(() => {
      expect(MockWebSocket.instances.length).toBeGreaterThan(0);
    });

    act(() => {
      lastWs().triggerOpen();
      // 推送非法 schema → hook 内部会 close + startPolling
      lastWs().triggerMessage(JSON.stringify({ wrong: "shape" }));
    });

    // 等待 close handler + REST 决策完成
    await vi.waitFor(() => {
      expect(result.current.pollingEnabled).toBe(true);
    });
    vi.useRealTimers();
  });
});

describe("useTaskRunner.reset", () => {
  it("把全部状态恢复到 idle 并关闭 WS", async () => {
    mocked.createTask.mockResolvedValue({ task_id: "tx", status: "pending" });
    mocked.getTask.mockResolvedValue({ task_id: "tx", status: "processing" });
    const { result } = renderHook(() => useTaskRunner());

    act(() => {
      result.current.startTask("/x");
    });
    await waitFor(() => {
      expect(result.current.taskId).toBe("tx");
    });

    act(() => {
      result.current.reset();
    });
    expect(result.current.taskId).toBeUndefined();
    expect(result.current.status).toBe("idle");
    expect(result.current.allResults).toEqual([]);
  });
});
