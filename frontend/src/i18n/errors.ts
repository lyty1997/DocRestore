/**
 * 错误本地化工具：把 ``ApiError`` / ``Error`` / ``unknown`` 转成
 * ``LocalizedError`` 结构，组件用 ``renderLocalized(err, t)`` 翻译显示。
 *
 * 设计目标：
 * - hook / api 层不依赖 ``useTranslation``（不在组件树里）
 * - 错误信息以 i18n key 透传到组件，组件用 ``t()`` 渲染
 * - 任意一层都可以保留中文 ``fallback`` 文本，避免 i18n 字典缺 key 时的空白
 */

import { ApiError, type ApiErrorParams } from "../api/client";
import type { TranslationFn } from "./config";

/** 本地化错误：携带 i18n key + 占位符值，UI 用 ``renderLocalized`` 渲染。 */
export interface LocalizedError {
  /** 主信息 i18n key（如 ``errors.api.task_not_found``）。 */
  readonly key: string;
  /** 主信息占位符值（与 key 模板里 ``{name}`` 对应）。 */
  readonly params?: ApiErrorParams;
  /** 诊断 hint i18n key（HTTP 状态码诊断 / 客户端兜底建议）。 */
  readonly hintKey?: string;
  /** hint 占位符值。 */
  readonly hintParams?: ApiErrorParams;
  /** i18n 翻译失败时的兜底中文（开发友好；正常路径不会用到）。 */
  readonly fallback?: string;
}

/** 把后端 ``APIErrorCode`` 字符串转成 i18n key。 */
function apiCodeToKey(code: string): string {
  return `errors.api.${code.toLowerCase()}`;
}

/** ApiError → LocalizedError */
export function fromApiError(err: ApiError): LocalizedError {
  const base: LocalizedError = (() => {
    if (err.code !== undefined) {
      return {
        key: apiCodeToKey(err.code),
        params: err.params,
        fallback: err.message,
      };
    }
    if (err.messageKey !== undefined) {
      return {
        key: err.messageKey,
        ...(err.messageKeyParams === undefined
          ? {}
          : { params: err.messageKeyParams }),
        fallback: err.message,
      };
    }
    return { key: "errors.unknown", fallback: err.message };
  })();

  if (err.hintKey === undefined) return base;
  return { ...base, hintKey: err.hintKey };
}

/** 任意错误对象 → LocalizedError。``fallbackKey`` 是非 ApiError 时使用的 i18n key。 */
export function fromUnknown(
  error_: unknown,
  fallbackKey = "errors.unknown",
): LocalizedError {
  if (error_ instanceof ApiError) return fromApiError(error_);
  if (error_ instanceof Error) {
    return { key: fallbackKey, fallback: error_.message };
  }
  return { key: fallbackKey, fallback: String(error_) };
}

/** 直接构造 ``LocalizedError``（hook 内手工抛错时用）。 */
export function localized(
  key: string,
  params?: ApiErrorParams,
): LocalizedError {
  return params === undefined ? { key } : { key, params };
}

/** 渲染：主信息（+ 可选 hint，用换行分隔）。 */
export function renderLocalized(
  err: LocalizedError,
  t: TranslationFn,
): string {
  const main = t(err.key, err.params);
  /* 翻译失败 → t() 回落到 key 字面量；这时若有 fallback 中文用 fallback */
  const mainText =
    main === err.key && err.fallback !== undefined ? err.fallback : main;
  if (err.hintKey === undefined) return mainText;
  const hint = t(err.hintKey, err.hintParams);
  return `${mainText}\n${hint}`;
}
