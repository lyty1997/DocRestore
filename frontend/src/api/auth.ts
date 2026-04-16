/**
 * API Token 管理（localStorage 持久化）
 *
 * 对应服务端环境变量 DOCRESTORE_API_TOKEN。
 * 未配置 token 时，所有函数返回空值，不影响无认证模式。
 */

const TOKEN_KEY = "docrestore_api_token";

/** 从 localStorage 加载 token */
export function loadApiToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

/** 保存 token 到 localStorage */
export function saveApiToken(token: string): void {
  const trimmed = token.trim();
  if (trimmed) {
    localStorage.setItem(TOKEN_KEY, trimmed);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

/** 清除已保存的 token */
export function clearApiToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/** 构建 Authorization header（token 为空时返回空对象） */
export function getAuthHeaders(): Record<string, string> {
  const token = loadApiToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 为 URL 附加 ?token= 参数（用于 <img src> / <a href> / WS 等无法设置 Header 的场景） */
export function appendTokenToUrl(url: string): string {
  const token = loadApiToken();
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}
