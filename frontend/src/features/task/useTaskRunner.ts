/**
 * 任务执行 hook：创建任务 → WS 订阅 → 轮询降级 → 拉取结果
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createTask,
  getTask,
  getTaskResults,
  getWsProgressUrl,
} from "../../api/client";
import {
  TaskProgressSchema,
  type TaskProgress,
  type TaskResultResponse,
} from "../../api/schemas";

/** 页面状态 */
type TaskStatus = "idle" | "pending" | "processing" | "completed" | "failed";

/** WS 连接状态 */
type WsState = "connecting" | "open" | "closed" | "error";

/** hook 返回值 */
interface UseTaskRunnerReturn {
  /** 当前任务 ID */
  taskId: string | undefined;
  /** 页面状态 */
  status: TaskStatus;
  /** 最新进度 */
  progress: TaskProgress | undefined;
  /** 完成后的 markdown 结果（第一篇，向下兼容） */
  resultMarkdown: string | undefined;
  /** 全部文档结果 */
  allResults: TaskResultResponse[];
  /** 结构化结果（第一篇，向下兼容） */
  taskResult: TaskResultResponse | undefined;
  /** 错误信息 */
  error: string | undefined;
  /** WS 连接状态 */
  wsState: WsState;
  /** 是否在轮询 */
  pollingEnabled: boolean;
  /** 启动任务 */
  startTask: (
    imageDir: string,
    outputDir?: string,
    llm?: {
      model?: string | undefined;
      api_base?: string | undefined;
      api_key?: string | undefined;
    },
    pii?: {
      enable: boolean;
      custom_sensitive_words?:
        | readonly { word: string; code?: string | undefined }[]
        | undefined;
    },
    ocr?: { model?: string | undefined; gpu_id?: string | undefined },
  ) => void;
  /** 重置到 idle */
  reset: () => void;
}

/** 轮询间隔（ms） */
const POLL_INTERVAL = 1000;

/** WS 连接超时（ms） */
const WS_CONNECT_TIMEOUT = 5000;

export function useTaskRunner(): UseTaskRunnerReturn {
  const [taskId, setTaskId] = useState<string | undefined>();
  const [status, setStatus] = useState<TaskStatus>("idle");
  const [progress, setProgress] = useState<TaskProgress | undefined>();
  const [resultMarkdown, setResultMarkdown] = useState<string | undefined>();
  const [allResults, setAllResults] = useState<TaskResultResponse[]>([]);
  const [taskResult, setTaskResult] = useState<
    TaskResultResponse | undefined
  >();
  const [error, setError] = useState<string | undefined>();
  const [wsState, setWsState] = useState<WsState>("closed");
  const [pollingEnabled, setPollingEnabled] = useState(false);

  // 用 ref 保存清理函数，避免闭包陈旧
  const wsRef = useRef<WebSocket | undefined>(undefined);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(
    undefined,
  );
  const wsTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const isMountedRef = useRef(true);

  /** 清理所有连接/定时器 */
  const cleanup = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = undefined;
    }
    if (pollTimerRef.current !== undefined) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = undefined;
    }
    if (wsTimeoutRef.current !== undefined) {
      clearTimeout(wsTimeoutRef.current);
      wsTimeoutRef.current = undefined;
    }
  }, []);

  // 卸载时清理
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      cleanup();
    };
  }, [cleanup]);

  /** 拉取最终结果 */
  const fetchResult = useCallback(async (tid: string) => {
    try {
      const resp = await getTaskResults(tid);
      if (isMountedRef.current) {
        setAllResults(resp.results);
        const first = resp.results[0];
        if (first !== undefined) {
          setResultMarkdown(first.markdown);
          setTaskResult(first);
        }
      }
    } catch {
      console.error("拉取结果失败");
    }
  }, []);

  /** 处理轮询响应的终态判断 */
  const handlePollResponse = useCallback(
    async (resp: Awaited<ReturnType<typeof getTask>>, tid: string) => {
      if (!isMountedRef.current) return;

      if (resp.progress) {
        setProgress(resp.progress);
      }

      switch (resp.status) {
        case "completed": {
          setStatus("completed");
          cleanup();
          setPollingEnabled(false);
          await fetchResult(tid);
          break;
        }
        case "failed": {
          setStatus("failed");
          setError(resp.error ?? "任务失败");
          cleanup();
          setPollingEnabled(false);
          break;
        }
        case "processing": {
          setStatus("processing");
          break;
        }
        default: {
          break;
        }
      }
    },
    [cleanup, fetchResult],
  );

  /** 启动轮询 */
  const startPolling = useCallback(
    (tid: string) => {
      if (pollTimerRef.current !== undefined) return; // 已在轮询
      setPollingEnabled(true);

      pollTimerRef.current = setInterval(() => {
        void (async () => {
          try {
            const resp = await getTask(tid);
            await handlePollResponse(resp, tid);
          } catch {
            // 轮询失败静默重试
          }
        })();
      }, POLL_INTERVAL);
    },
    [handlePollResponse],
  );

  /** 建立 WS 连接 */
  const connectWs = useCallback(
    (tid: string) => {
      setWsState("connecting");

      const ws = new WebSocket(getWsProgressUrl(tid));
      wsRef.current = ws;

      // 连接超时
      wsTimeoutRef.current = setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) {
          ws.close();
          if (isMountedRef.current) {
            setWsState("error");
            startPolling(tid);
          }
        }
      }, WS_CONNECT_TIMEOUT);

      ws.addEventListener("open", () => {
        if (!isMountedRef.current) return;
        if (wsTimeoutRef.current !== undefined) {
          clearTimeout(wsTimeoutRef.current);
          wsTimeoutRef.current = undefined;
        }
        setWsState("open");
      });

      ws.addEventListener("message", (event: MessageEvent<unknown>) => {
        if (!isMountedRef.current) return;
        try {
          const data: unknown =
            typeof event.data === "string"
              ? (JSON.parse(event.data) as unknown)
              : event.data;
          const parsed = TaskProgressSchema.parse(data);
          setProgress(parsed);
          setStatus("processing");
        } catch {
          // schema 校验失败，降级到轮询
          console.error("WS 消息校验失败，降级到轮询");
          ws.close();
          startPolling(tid);
        }
      });

      ws.addEventListener("close", () => {
        if (!isMountedRef.current) return;
        setWsState("closed");
        wsRef.current = undefined;

        // WS 关闭后通过 REST 确认终态
        void (async () => {
          try {
            const resp = await getTask(tid);
            if (!isMountedRef.current) return;

            switch (resp.status) {
              case "completed": {
                setStatus("completed");
                cleanup();
                await fetchResult(tid);
                break;
              }
              case "failed": {
                setStatus("failed");
                setError(resp.error ?? "任务失败");
                cleanup();
                break;
              }
              default: {
                // 未到终态，启动轮询
                startPolling(tid);
                break;
              }
            }
          } catch {
            // REST 也失败了，启动轮询兜底
            startPolling(tid);
          }
        })();
      });

      ws.addEventListener("error", () => {
        if (!isMountedRef.current) return;
        setWsState("error");
        // error 事件后通常会触发 close，由 close handler 处理降级
      });
    },
    [cleanup, fetchResult, startPolling],
  );

  /** 启动任务 */
  const startTask = useCallback(
    (
      imageDir: string,
      outputDir?: string,
      llm?: {
        model?: string | undefined;
        api_base?: string | undefined;
        api_key?: string | undefined;
      },
      pii?: {
        enable: boolean;
        custom_sensitive_words?:
          | readonly { word: string; code?: string | undefined }[]
          | undefined;
      },
      ocr?: { model?: string | undefined; gpu_id?: string | undefined },
    ) => {
      // 重置状态
      cleanup();
      setStatus("pending");
      setProgress(undefined);
      setResultMarkdown(undefined);
      setAllResults([]);
      setTaskResult(undefined);
      setError(undefined);
      setWsState("closed");
      setPollingEnabled(false);

      void (async () => {
        try {
          const resp = await createTask({
            image_dir: imageDir,
            output_dir: outputDir,
            llm,
            pii,
            ocr,
          });
          if (!isMountedRef.current) return;
          setTaskId(resp.task_id);
          setStatus("processing");
          connectWs(resp.task_id);
        } catch (error_: unknown) {
          if (!isMountedRef.current) return;
          setStatus("failed");
          setError(
            error_ instanceof Error ? error_.message : "创建任务失败",
          );
        }
      })();
    },
    [cleanup, connectWs],
  );

  /** 重置 */
  const reset = useCallback(() => {
    cleanup();
    setTaskId(undefined);
    setStatus("idle");
    setProgress(undefined);
    setResultMarkdown(undefined);
    setAllResults([]);
    setTaskResult(undefined);
    setError(undefined);
    setWsState("closed");
    setPollingEnabled(false);
  }, [cleanup]);

  return {
    taskId,
    status,
    progress,
    resultMarkdown,
    allResults,
    taskResult,
    error,
    wsState,
    pollingEnabled,
    startTask,
    reset,
  };
}
