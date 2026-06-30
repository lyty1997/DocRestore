/**
 * 鉴权状态 hook：拉取公共 /auth/info + 读取本地 token，派生"是否该提示设置 token"。
 *
 * 用途：
 * - 服务无鉴权（insecure）时不打扰用户（不弹"缺少 token"提示）。
 * - 服务要求 token 但本地没存时，提示用户去设置，并按 token 来源给获取指引。
 */

import { useCallback, useEffect, useState } from "react";

import { loadApiToken } from "../api/auth";
import { getAuthInfo } from "../api/client";
import type { TokenSource } from "../api/schemas";

export interface AuthStatus {
  /** /auth/info 是否仍在加载（首次解析前为 true）。 */
  readonly loading: boolean;
  /** 服务是否要求 token；undefined = 尚未拿到 /auth/info（未知）。 */
  readonly authRequired: boolean | undefined;
  /** token 来源枚举；undefined = 尚未拿到 /auth/info。 */
  readonly tokenSource: TokenSource | undefined;
  /** 本地 localStorage 是否已保存 token。 */
  readonly hasToken: boolean;
  /** 是否应提示用户设置 token：服务**明确**要求 token 且本地没存。 */
  readonly needsToken: boolean;
  /** 重新读取本地 token + 重新拉 /auth/info（保存/清除 token 后调用以同步提示）。 */
  readonly refresh: () => void;
}

export function useAuthStatus(): AuthStatus {
  // 无参 useState 重载：初值即 undefined（不显式传 undefined，避免 no-useless-undefined）。
  const [authRequired, setAuthRequired] = useState<boolean | undefined>();
  const [tokenSource, setTokenSource] = useState<TokenSource | undefined>();
  const [loading, setLoading] = useState(true);
  const [hasToken, setHasToken] = useState<boolean>(() => loadApiToken() !== "");
  // 自增触发器：refresh() 改它 → 拉取 effect 重跑（effect 内 cancelled 标志防竞态）。
  const [reloadTick, setReloadTick] = useState(0);

  const refresh = useCallback(() => {
    // 同步态在事件回调里更新（不在 effect 内同步 setState，避免级联渲染告警）：
    // 重新读本地 token + 置 loading + 触发 effect 重新拉 /auth/info。
    setHasToken(loadApiToken() !== "");
    setLoading(true);
    setReloadTick((n) => n + 1);
  }, []);

  useEffect(() => {
    // 仅做异步拉取（结果在 promise 回调里落 state，非同步 setState）：
    // 首挂载 loading 初值即 true、hasToken 由惰性初值给出，无需在此同步 setState。
    let cancelled = false;
    void getAuthInfo()
      .then((info) => {
        if (cancelled) return;
        setAuthRequired(info.auth_required);
        setTokenSource(info.token_source);
      })
      .catch(() => {
        // /auth/info 拉取失败（后端不可达等）：authRequired 留 undefined，
        // 不弹横幅（避免误报），但也不静默假装无需鉴权——真要鉴权时用户仍会
        // 从请求 401 的错误提示被引导到 Token 设置。
        if (cancelled) return;
        setAuthRequired(undefined);
        setTokenSource(undefined);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadTick]);

  // 仅在服务明确要求 token（authRequired===true）且本地无 token 时提示；
  // authRequired 为 undefined（未知/拉取失败）时不弹横幅，避免打扰。
  const needsToken = authRequired === true && !hasToken;

  return { loading, authRequired, tokenSource, hasToken, needsToken, refresh };
}
