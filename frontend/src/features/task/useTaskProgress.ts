/**
 * useTaskProgress — 按 taskId 订阅实时进度（WS + 轮询降级）。
 *
 * 与 `useTaskRunner` 的差别：
 * - 不负责**创建**任务（用于 resume/retry 后或打开历史 task 时的"附加订阅"）
 * - 仅订阅进度并发布到外部；拉结果、刷新列表等副作用由调用方通过 `onTerminal`
 *   回调自己处理，保持 hook 纯粹
 * - `taskId` 变化（如 resume 后自动切到新 task）时自动重新订阅
 *
 * 终态判断：WS 收到 close 或状态推送后确认任务进入 completed/failed，
 * 触发 `onTerminal` 回调一次，调用方据此更新 UI（刷新任务信息、拉结果等）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getTask, getWsProgressUrl } from "../../api/client";
import { TaskProgressSchema } from "../../api/schemas";
import {
  mergeProgressFrame,
  type ProgressBuckets,
} from "./progressPhase";

type WsState = "connecting" | "open" | "closed" | "error";
type TrackedStatus = "pending" | "processing" | "completed" | "failed" | "unknown";

interface UseTaskProgressOptions {
  /** taskId 为 undefined 时不订阅（占位） */
  readonly taskId: string | undefined;
  /**
   * 是否启用订阅。传入历史 task 时如果业务层已知是 completed/failed，可直接
   * 传 false 避免无谓 WS 连接；hook 内部也会在收到终态时自动停。
   */
  readonly enabled: boolean;
  /**
   * 进入终态时（completed/failed）触发一次，调用方用于刷新任务元信息、拉结果等。
   * 同一 taskId 的同一次订阅生命周期内最多触发一次；taskId 切换后重新武装。
   */
  readonly onTerminal?: (status: "completed" | "failed") => void;
}

interface UseTaskProgressReturn {
  readonly progresses: ProgressBuckets;
  readonly status: TrackedStatus;
  readonly wsState: WsState;
  readonly pollingEnabled: boolean;
}

const POLL_INTERVAL = 1000;
const WS_CONNECT_TIMEOUT = 5000;

export function useTaskProgress(
  options: UseTaskProgressOptions,
): UseTaskProgressReturn {
  const { taskId, enabled, onTerminal } = options;

  const [progresses, setProgresses] = useState<ProgressBuckets>({});
  const [status, setStatus] = useState<TrackedStatus>("unknown");
  const [wsState, setWsState] = useState<WsState>("closed");
  const [pollingEnabled, setPollingEnabled] = useState(false);

  const wsRef = useRef<WebSocket | undefined>(undefined);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(
    undefined,
  );
  const wsTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const mountedRef = useRef(true);
  /** 本订阅周期内是否已触发过 onTerminal，避免重复 */
  const terminalFiredRef = useRef(false);
  /** 用 ref 持有最新 onTerminal，避免因为 onTerminal 引用变化导致重订阅 */
  const onTerminalRef = useRef(onTerminal);
  useEffect(() => {
    onTerminalRef.current = onTerminal;
  }, [onTerminal]);

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

  const fireTerminal = useCallback(
    (kind: "completed" | "failed") => {
      if (terminalFiredRef.current) return;
      terminalFiredRef.current = true;
      onTerminalRef.current?.(kind);
    },
    [],
  );

  const applyRestStatus = useCallback(
    async (tid: string): Promise<"terminal" | "pending"> => {
      /* WS 关闭或降级时用 REST 兜底判断终态。返回 terminal 表示不需继续轮询。 */
      try {
        const resp = await getTask(tid);
        if (!mountedRef.current) return "terminal";
        if (resp.progress) {
          const frame = resp.progress;
          setProgresses((prev) => mergeProgressFrame(prev, frame));
        }
        switch (resp.status) {
          case "completed": {
            setStatus("completed");
            fireTerminal("completed");
            return "terminal";
          }
          case "failed": {
            setStatus("failed");
            fireTerminal("failed");
            return "terminal";
          }
          case "processing": {
            setStatus("processing");
            return "pending";
          }
          case "pending": {
            setStatus("pending");
            return "pending";
          }
          default: {
            return "pending";
          }
        }
      } catch {
        return "pending";
      }
    },
    [fireTerminal],
  );

  const startPolling = useCallback(
    (tid: string) => {
      if (pollTimerRef.current !== undefined) return;
      setPollingEnabled(true);
      pollTimerRef.current = setInterval(() => {
        void (async () => {
          const r = await applyRestStatus(tid);
          if (r === "terminal") {
            cleanup();
            setPollingEnabled(false);
          }
        })();
      }, POLL_INTERVAL);
    },
    [applyRestStatus, cleanup],
  );

  const connectWs = useCallback(
    (tid: string) => {
      setWsState("connecting");
      const ws = new WebSocket(getWsProgressUrl(tid));
      wsRef.current = ws;

      wsTimeoutRef.current = setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) {
          ws.close();
          if (mountedRef.current) {
            setWsState("error");
            startPolling(tid);
          }
        }
      }, WS_CONNECT_TIMEOUT);

      ws.addEventListener("open", () => {
        if (!mountedRef.current) return;
        if (wsTimeoutRef.current !== undefined) {
          clearTimeout(wsTimeoutRef.current);
          wsTimeoutRef.current = undefined;
        }
        setWsState("open");
      });

      ws.addEventListener("message", (event: MessageEvent<unknown>) => {
        if (!mountedRef.current) return;
        try {
          const raw: unknown =
            typeof event.data === "string"
              ? (JSON.parse(event.data) as unknown)
              : event.data;
          const parsed = TaskProgressSchema.parse(raw);
          setProgresses((prev) => mergeProgressFrame(prev, parsed));
          /* 进度帧的 stage 可能是 "completed"/"failed"；WS close 后 REST 才是权威 */
          if (parsed.stage === "completed" || parsed.stage === "failed") {
            /* 记住状态但不触发 terminal — 让 close handler 的 REST 兜底确认 */
            setStatus(parsed.stage);
          } else {
            setStatus("processing");
          }
        } catch {
          console.error("WS 消息校验失败，降级到轮询");
          ws.close();
          startPolling(tid);
        }
      });

      ws.addEventListener("close", () => {
        if (!mountedRef.current) return;
        setWsState("closed");
        wsRef.current = undefined;
        /* WS close 后 REST 确认终态；非终态则启动轮询 */
        void (async () => {
          const r = await applyRestStatus(tid);
          if (r === "pending" && mountedRef.current) {
            startPolling(tid);
          }
        })();
      });

      ws.addEventListener("error", () => {
        if (!mountedRef.current) return;
        setWsState("error");
        /* error 后通常跟 close，由 close handler 统一处理降级 */
      });
    },
    [applyRestStatus, startPolling],
  );

  /** taskId 或 enabled 变化时重新订阅 */
  useEffect(() => {
    mountedRef.current = true;
    /* enabled=false 或无 taskId：什么都不做 */
    if (!enabled || taskId === undefined) {
      return (): void => {
        mountedRef.current = false;
        cleanup();
      };
    }

    /* 新订阅周期：重置状态 + 允许再次触发 onTerminal */
    terminalFiredRef.current = false;
    setProgresses({});
    setStatus("pending");
    setWsState("closed");
    setPollingEnabled(false);

    /* 先查一次 REST：已经 terminal 就直接触发回调不订 WS */
    void (async () => {
      const r = await applyRestStatus(taskId);
      if (r === "terminal" || !mountedRef.current) return;
      connectWs(taskId);
    })();

    return (): void => {
      mountedRef.current = false;
      cleanup();
    };
    /* eslint-disable-next-line react-hooks/exhaustive-deps -- 订阅只依赖 taskId/enabled */
  }, [taskId, enabled]);

  return { progresses, status, wsState, pollingEnabled };
}
