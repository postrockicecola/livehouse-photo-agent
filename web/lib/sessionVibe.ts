export type SessionVibeState = {
  prompt: string;
  film_variant: string;
  label_zh: string;
  reason_zh?: string;
  matched_by?: string;
  matched?: boolean;
  updated_unix?: number;
};

export function sessionVibeMatched(sv: SessionVibeState | null | undefined): boolean {
  if (!sv) return false;
  if (typeof sv.matched === "boolean") return sv.matched;
  const mb = (sv.matched_by ?? "").trim();
  return mb !== "rules:fallback" && mb !== "rules:default" && mb !== "llm:failed";
}

export type SessionVibeGetResponse = {
  active: boolean;
  session_vibe: SessionVibeState | null;
  previews_dir?: string;
};

export type VibeResolveResponse = {
  decision: SessionVibeState & { reason_zh: string; matched_by: string };
};

export async function fetchSessionVibe(apiBase: string): Promise<SessionVibeGetResponse> {
  const res = await fetch(`${apiBase}/api/vibe/session`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`读取会话风格失败 HTTP ${res.status}`);
  }
  return res.json() as Promise<SessionVibeGetResponse>;
}

export async function saveSessionVibe(apiBase: string, prompt: string): Promise<SessionVibeGetResponse> {
  const res = await fetch(`${apiBase}/api/vibe/session`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: prompt.trim(), clear: false }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `保存会话风格失败 HTTP ${res.status}`);
  }
  return res.json() as Promise<SessionVibeGetResponse>;
}

export async function clearSessionVibeApi(apiBase: string): Promise<SessionVibeGetResponse> {
  const res = await fetch(`${apiBase}/api/vibe/session`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: "", clear: true }),
  });
  if (!res.ok) {
    throw new Error(`清除会话风格失败 HTTP ${res.status}`);
  }
  return res.json() as Promise<SessionVibeGetResponse>;
}

export async function resolveVibePrompt(apiBase: string, prompt: string): Promise<VibeResolveResponse> {
  const res = await fetch(`${apiBase}/api/vibe/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: prompt.trim() }),
  });
  if (!res.ok) {
    throw new Error(`解析风格失败 HTTP ${res.status}`);
  }
  return res.json() as Promise<VibeResolveResponse>;
}
