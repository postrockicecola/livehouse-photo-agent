/**
 * Client for the Gallery copilot API (`POST /api/agent/chat`).
 *
 * Calls go through the same `${API_BASE}/api/*` path the rest of the gallery uses
 * (Next rewrites proxy to FastAPI when `NEXT_PUBLIC_API_BASE` is empty).
 */

export type AgentToolCall = {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
};

export type AgentGuardrailEvent = {
  kind: string;
  triggered: boolean;
  matches: string[];
  detail?: Record<string, unknown>;
};

export type AgentChatResponse = {
  reply: string;
  tool_calls: AgentToolCall[];
  guardrail_events: AgentGuardrailEvent[];
  memory_turns: number;
  base_dir: string;
  error?: string | null;
};

export type AgentChatRequest = {
  session_id: string;
  message: string;
  previews_dir?: string | null;
  reset?: boolean;
};

export async function sendAgentChat(
  apiBase: string,
  body: AgentChatRequest,
): Promise<AgentChatResponse> {
  const res = await fetch(`${apiBase}/api/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await res.text();
  let data: AgentChatResponse;
  try {
    data = text ? (JSON.parse(text) as AgentChatResponse) : ({} as AgentChatResponse);
  } catch {
    throw new Error(`agent 返回非 JSON（HTTP ${res.status}）。请确认 gallery_server 已启动。`);
  }
  if (!res.ok && !data?.error) {
    throw new Error(`agent 请求失败（HTTP ${res.status}）`);
  }
  return data;
}

/** Stable per-tab session id (so server-side conversation memory threads turns). */
export function newSessionId(): string {
  try {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
      return crypto.randomUUID();
    }
  } catch {
    /* ignore */
  }
  return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
