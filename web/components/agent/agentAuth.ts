/**
 * Client for the agent auth API (`/api/auth/*`) + token storage.
 *
 * Tokens live in localStorage and are sent as `Authorization: Bearer <token>` on chat
 * requests, so a logged-in user's conversation history is loaded from / saved to their
 * own server-side bucket. Anonymous use still works (no token → anon conversation).
 */

const TOKEN_KEY = "livehouse.agent.token";
const USER_KEY = "livehouse.agent.user";

export type AuthUser = { id: number; username: string; created_at?: number };

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

function persistSession(token: string, user: AuthUser) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* ignore */
  }
}

function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

/** Authorization header for the current token (empty object when anonymous). */
export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

type AuthApiResponse = { token?: string; user?: AuthUser; error?: string };

async function postAuth(
  apiBase: string,
  path: string,
  body: Record<string, unknown>,
): Promise<AuthApiResponse> {
  const res = await fetch(`${apiBase}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await res.text();
  let data: AuthApiResponse;
  try {
    data = text ? (JSON.parse(text) as AuthApiResponse) : {};
  } catch {
    throw new Error(`认证服务返回非 JSON（HTTP ${res.status}）`);
  }
  return data;
}

export async function registerUser(
  apiBase: string,
  username: string,
  password: string,
): Promise<AuthUser> {
  const data = await postAuth(apiBase, "/api/auth/register", { username, password });
  if (data.error || !data.token || !data.user) {
    throw new Error(data.error || "注册失败");
  }
  persistSession(data.token, data.user);
  return data.user;
}

export async function loginUser(
  apiBase: string,
  username: string,
  password: string,
): Promise<AuthUser> {
  const data = await postAuth(apiBase, "/api/auth/login", { username, password });
  if (data.error || !data.token || !data.user) {
    throw new Error(data.error || "登录失败");
  }
  persistSession(data.token, data.user);
  return data.user;
}

export async function logoutUser(apiBase: string): Promise<void> {
  try {
    await fetch(`${apiBase}/api/auth/logout`, {
      method: "POST",
      headers: { ...authHeader() },
      cache: "no-store",
    });
  } catch {
    /* best effort */
  }
  clearSession();
}

/** Validate the stored token against the server; clears it if invalid. */
export async function fetchMe(apiBase: string): Promise<AuthUser | null> {
  const token = getToken();
  if (!token) return null;
  try {
    const res = await fetch(`${apiBase}/api/auth/me`, {
      headers: { ...authHeader() },
      cache: "no-store",
    });
    const data = (await res.json()) as AuthApiResponse;
    if (data.user) return data.user;
  } catch {
    return getStoredUser();
  }
  clearSession();
  return null;
}
