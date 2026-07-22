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
  metadata?: Record<string, unknown>;
};

export type AgentGuardrailEvent = {
  kind: string;
  triggered: boolean;
  matches: string[];
  detail?: Record<string, unknown>;
};

import { authHeader } from "@/components/agent/agentAuth";

export type AgentChatResponse = {
  reply: string;
  tool_calls: AgentToolCall[];
  guardrail_events: AgentGuardrailEvent[];
  memory_turns: number;
  base_dir: string;
  error?: string | null;
};

export type AgentMode = "gallery" | "general";

export type AgentChatRequest = {
  session_id: string;
  message: string;
  previews_dir?: string | null;
  reset?: boolean;
  mode?: AgentMode;
};

export async function sendAgentChat(
  apiBase: string,
  body: AgentChatRequest,
): Promise<AgentChatResponse> {
  const res = await fetch(`${apiBase}/api/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
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

export type AgentStreamDone = {
  reply: string;
  tool_calls: AgentToolCall[];
  guardrail_events: AgentGuardrailEvent[];
  memory_turns: number;
  base_dir: string;
};

export type AgentStreamCallbacks = {
  onToken?: (text: string) => void;
  onToolCall?: (call: AgentToolCall) => void;
  onGuardrail?: (ev: AgentGuardrailEvent) => void;
  onDone?: (info: AgentStreamDone) => void;
  onError?: (message: string) => void;
};

/**
 * Stream a copilot turn over SSE (`POST /api/agent/chat/stream`).
 *
 * Resolves when the stream ends. `receivedToken` in the returned summary lets the
 * caller decide whether a hard failure should fall back to the non-streaming API.
 */
export async function streamAgentChat(
  apiBase: string,
  body: AgentChatRequest,
  cb: AgentStreamCallbacks,
): Promise<{ receivedToken: boolean }> {
  const res = await fetch(`${apiBase}/api/agent/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...authHeader() },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok || !res.body) {
    throw new Error(`agent 流式请求失败（HTTP ${res.status}）`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let receivedToken = false;

  const handle = (payload: string) => {
    if (!payload) return;
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(payload) as Record<string, unknown>;
    } catch {
      return;
    }
    switch (ev.type) {
      case "token":
        receivedToken = true;
        cb.onToken?.(String(ev.text ?? ""));
        break;
      case "tool_call":
        cb.onToolCall?.({
          tool: String(ev.tool ?? ""),
          args: (ev.args as Record<string, unknown>) ?? {},
          ok: Boolean(ev.ok),
          metadata: (ev.metadata as Record<string, unknown>) || undefined,
        });
        break;
      case "guardrail":
        cb.onGuardrail?.(ev as unknown as AgentGuardrailEvent);
        break;
      case "done":
        cb.onDone?.({
          reply: String(ev.reply ?? ""),
          tool_calls: (ev.tool_calls as AgentToolCall[]) ?? [],
          guardrail_events: (ev.guardrail_events as AgentGuardrailEvent[]) ?? [],
          memory_turns: Number(ev.memory_turns ?? 0),
          base_dir: String(ev.base_dir ?? ""),
        });
        break;
      case "error":
        cb.onError?.(String(ev.error ?? "agent 出错"));
        break;
      default:
        break;
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE events are separated by a blank line.
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const rawEvent = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const dataLine = rawEvent
        .split("\n")
        .find((l) => l.startsWith("data:"));
      if (dataLine) handle(dataLine.slice("data:".length).trim());
    }
  }
  // Flush any trailing event without a terminating blank line.
  const tail = buf.split("\n").find((l) => l.startsWith("data:"));
  if (tail) handle(tail.slice("data:".length).trim());

  return { receivedToken };
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

export type AgentHistoryTurn = { role: "user" | "assistant"; text: string };

/** Load the persisted transcript for a (session, mode) so the UI can restore it. */
export async function fetchAgentHistory(
  apiBase: string,
  sessionId: string,
  mode: AgentMode,
): Promise<AgentHistoryTurn[]> {
  try {
    const res = await fetch(
      `${apiBase}/api/agent/history?session_id=${encodeURIComponent(sessionId)}&mode=${mode}`,
      { headers: { ...authHeader() }, cache: "no-store" },
    );
    if (!res.ok) return [];
    const data = (await res.json()) as { messages?: { role: string; content: string }[] };
    return (data.messages ?? [])
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role as "user" | "assistant", text: m.content }));
  } catch {
    return [];
  }
}

function sessionKey(context: string, mode: string): string {
  return `livehouse.agent.session.${context}.${mode}`;
}

/**
 * Session id persisted in localStorage per (context, mode) so a conversation resumes
 * across reloads (server memory is keyed by session id). Falls back to a fresh id
 * when storage is unavailable.
 */
export function persistentSessionId(context: string, mode: string): string {
  try {
    const key = sessionKey(context, mode);
    let v = localStorage.getItem(key);
    if (!v) {
      v = `${context}-${mode}-${newSessionId()}`;
      localStorage.setItem(key, v);
    }
    return v;
  } catch {
    return `${context}-${mode}-${newSessionId()}`;
  }
}

/** Start a brand-new conversation for (context, mode); returns the new session id. */
export function rotateSessionId(context: string, mode: string): string {
  const v = `${context}-${mode}-${newSessionId()}`;
  try {
    localStorage.setItem(sessionKey(context, mode), v);
  } catch {
    /* ignore */
  }
  return v;
}
