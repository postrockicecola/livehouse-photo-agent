"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  newSessionId,
  sendAgentChat,
  type AgentGuardrailEvent,
  type AgentToolCall,
} from "@/components/agent/agentChat";

type ChatTurn = {
  role: "user" | "assistant";
  text: string;
  toolCalls?: AgentToolCall[];
  guardrails?: AgentGuardrailEvent[];
  error?: boolean;
};

const SUGGESTIONS = [
  "这个 session 有多少张照片？分布如何？",
  "给我 90 分以上的精选片",
  "为什么某张被判为 trash？",
];

function ToolChip({ call }: { call: AgentToolCall }) {
  const argStr = useMemo(() => {
    try {
      const s = JSON.stringify(call.args ?? {});
      return s === "{}" ? "" : s;
    } catch {
      return "";
    }
  }, [call.args]);
  return (
    <span
      title={argStr ? `args: ${argStr}` : undefined}
      className="inline-flex items-center gap-1 rounded-[3px] border border-white/[0.08] bg-white/[0.04] px-1.5 py-0.5 text-[11px] text-white/55"
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${call.ok ? "bg-emerald-400/90" : "bg-rose-400/90"}`}
        aria-hidden
      />
      <span className="font-mono">{call.tool}</span>
    </span>
  );
}

function GuardrailChip({ ev }: { ev: AgentGuardrailEvent }) {
  return (
    <span
      title={ev.matches?.length ? ev.matches.join(", ") : ev.kind}
      className="inline-flex items-center gap-1 rounded-[3px] border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-100/80"
    >
      <span aria-hidden>⚠</span>
      <span className="font-mono">{ev.kind}</span>
    </span>
  );
}

export function ChatDock({
  apiBase,
  previewsDir,
  context = "gallery",
}: {
  apiBase: string;
  previewsDir?: string | null;
  context?: string;
}) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const sessionIdRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  if (!sessionIdRef.current) {
    sessionIdRef.current = `${context}-${newSessionId()}`;
  }

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, open, sending]);

  const send = useCallback(
    async (raw: string) => {
      const message = raw.trim();
      if (!message || sending) return;
      setInput("");
      setTurns((prev) => [...prev, { role: "user", text: message }]);
      setSending(true);
      try {
        const data = await sendAgentChat(apiBase, {
          session_id: sessionIdRef.current,
          message,
          previews_dir: previewsDir ?? undefined,
        });
        if (data.error) {
          setTurns((prev) => [...prev, { role: "assistant", text: data.error as string, error: true }]);
        } else {
          setTurns((prev) => [
            ...prev,
            {
              role: "assistant",
              text: data.reply || "(空回复)",
              toolCalls: data.tool_calls,
              guardrails: data.guardrail_events,
            },
          ]);
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "请求失败";
        setTurns((prev) => [...prev, { role: "assistant", text: msg, error: true }]);
      } finally {
        setSending(false);
      }
    },
    [apiBase, previewsDir, sending],
  );

  const resetChat = useCallback(() => {
    setTurns([]);
    sessionIdRef.current = `${context}-${newSessionId()}`;
  }, [context]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="打开 Gallery 助手"
        className="fixed bottom-4 right-4 z-50 flex h-11 items-center gap-2 rounded-full border border-white/[0.1] bg-white/[0.08] px-4 text-[13px] text-white/80 shadow-lg backdrop-blur-md transition-colors hover:bg-white/[0.14]"
      >
        <span className="h-2 w-2 rounded-full bg-emerald-400/90 shadow-[0_0_10px_rgba(52,211,153,0.5)]" aria-hidden />
        策展助手
      </button>
    );
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 flex h-[min(560px,calc(100vh-2rem))] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-[8px] border border-white/[0.1] bg-[#0d0d0d]/95 shadow-2xl backdrop-blur-md">
      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.06] px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-emerald-400/90 shadow-[0_0_10px_rgba(52,211,153,0.5)]" aria-hidden />
          <span className="text-[13px] font-medium text-white/80">策展助手</span>
          <span className="text-[11px] text-white/30">Gallery Copilot</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={resetChat}
            title="清空对话"
            className="rounded-[3px] px-1.5 py-0.5 text-[12px] text-white/35 hover:text-white/60"
          >
            清空
          </button>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="收起助手"
            className="rounded-[3px] px-1.5 py-0.5 text-[14px] text-white/40 hover:text-white/70"
          >
            ×
          </button>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {turns.length === 0 ? (
          <div className="space-y-3 pt-2">
            <p className="text-[12px] leading-relaxed text-white/35">
              我可以基于当前 session 的分析结果回答关于评分、标签、保留/丢弃的问题（只读，不会改动文件）。
            </p>
            <div className="flex flex-col gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="rounded-[4px] border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-left text-[12px] text-white/55 transition-colors hover:bg-white/[0.05] hover:text-white/75"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          turns.map((t, i) => (
            <div key={i} className={t.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={[
                  "max-w-[88%] rounded-[6px] px-2.5 py-2 text-[13px] leading-relaxed whitespace-pre-wrap break-words",
                  t.role === "user"
                    ? "bg-white/[0.1] text-white/85"
                    : t.error
                      ? "border border-rose-500/25 bg-rose-500/10 text-rose-100/85"
                      : "border border-white/[0.06] bg-white/[0.03] text-white/75",
                ].join(" ")}
              >
                <div>{t.text}</div>
                {(t.toolCalls?.length || t.guardrails?.length) ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {t.toolCalls?.map((c, j) => <ToolChip key={`t${j}`} call={c} />)}
                    {t.guardrails?.map((g, j) => <GuardrailChip key={`g${j}`} ev={g} />)}
                  </div>
                ) : null}
              </div>
            </div>
          ))
        )}
        {sending ? (
          <div className="flex justify-start">
            <div className="rounded-[6px] border border-white/[0.06] bg-white/[0.03] px-2.5 py-2 text-[13px] text-white/40">
              思考中…
            </div>
          </div>
        ) : null}
      </div>

      <div className="shrink-0 border-t border-white/[0.06] p-2.5">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(input);
              }
            }}
            rows={1}
            placeholder="问问这个 session 的照片…"
            className="max-h-28 min-h-[38px] flex-1 resize-none rounded-[5px] border border-white/[0.08] bg-white/[0.04] px-2.5 py-2 text-[13px] text-white/80 placeholder:text-white/28 focus:border-white/[0.14] focus:outline-none"
          />
          <button
            type="button"
            disabled={sending || !input.trim()}
            onClick={() => void send(input)}
            className="h-[38px] shrink-0 rounded-[5px] border border-white/[0.1] bg-white/[0.08] px-3 text-[13px] text-white/75 transition-colors hover:bg-white/[0.14] disabled:opacity-35"
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
