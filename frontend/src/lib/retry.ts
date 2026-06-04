/**
 * 挂载期请求退避重试助手。
 *
 * 后端启动慢于前端时，挂载期的 GET（GPU 列表 / OCR 状态 / 任务列表）会因
 * 后端未监听而 ECONNREFUSED（Vite 代理转成 502，浏览器侧 fetch 抛错）。
 * 若一次失败就放弃，后端随后就绪也不会自动恢复，必须重启前端。
 *
 * 本助手退避重试给定异步任务，直到其成功（不抛异常）即停止，让界面在后端
 * 就绪后自动恢复、无需手动刷新；并把"瞬间猛刷一串错误"摊成少量间隔重试。
 */

/** 默认退避间隔（ms），末值循环。初期快重试、之后退避到 8s 降低日志噪音。 */
const DEFAULT_RETRY_DELAYS_MS: readonly number[] = [1000, 2000, 4000, 8000];

/**
 * 退避重试 ``task`` 直到成功（resolve 视为成功并停止；reject 触发下一次重试）。
 *
 * @param task 异步任务；入参 ``isCancelled`` 供 await 后判断是否已卸载，
 *   卸载后应提前 return 不再 setState。任务内部**不要吞掉**网络异常——
 *   reject 才能触发重试；业务上希望停止重试时正常 resolve 即可。
 * @param delaysMs 退避间隔序列，按尝试次数取值、末值循环。
 * @returns cleanup 函数：在 effect 卸载时调用以取消挂起的重试。
 */
export function retryUntilSuccess(
  task: (isCancelled: () => boolean) => Promise<void>,
  delaysMs: readonly number[] = DEFAULT_RETRY_DELAYS_MS,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;

  const run = (): void => {
    if (cancelled) return;
    void task(() => cancelled).then(
      () => {
        /* 成功：停止重试 */
      },
      () => {
        if (cancelled) return;
        const delay =
          delaysMs[Math.min(attempt, delaysMs.length - 1)] ?? 8000;
        attempt += 1;
        timer = setTimeout(run, delay);
      },
    );
  };

  run();

  return (): void => {
    cancelled = true;
    if (timer !== undefined) clearTimeout(timer);
  };
}
