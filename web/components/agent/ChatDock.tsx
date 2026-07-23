"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAgentHistory,
  persistentSessionId,
  rotateSessionId,
  sendAgentChat,
  streamAgentChat,
  type AgentGuardrailEvent,
  type AgentMode,
  type AgentToolCall,
} from "@/components/agent/agentChat";
import {
  fetchMe,
  getStoredUser,
  loginUser,
  logoutUser,
  registerUser,
  type AuthUser,
} from "@/components/agent/agentAuth";
import {
  ShowcasePreviewModal,
  type ShowcasePreviewItem,
} from "@/components/agent/ShowcasePreviewModal";

type ChatTurn = {
  role: "user" | "assistant";
  text: string;
  toolCalls?: AgentToolCall[];
  guardrails?: AgentGuardrailEvent[];
  error?: boolean;
  streaming?: boolean;
};

/** Collect basenames from search/select metadata in this turn (for vibe preview pool). */
function filesFromToolCalls(calls: AgentToolCall[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const call of calls) {
    if (!call?.ok) continue;
    const meta = call.metadata ?? {};
    const raw = meta.files ?? meta.selected_keys;
    if (!Array.isArray(raw)) continue;
    for (const f of raw) {
      const name = String(f || "").trim();
      if (!name || seen.has(name)) continue;
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

/** Notify Gallery page to reload curation / vibe after write skills. */
function emitGalleryUiActions(
  calls: AgentToolCall[],
  turnContext: AgentToolCall[] = calls,
) {
  if (typeof window === "undefined") return;
  const turnFiles = filesFromToolCalls(turnContext);
  for (const call of calls) {
    if (!call?.ok) continue;
    const action = String(call.metadata?.ui_action || "");
    if (!action) continue;
    const meta = { ...(call.metadata ?? {}) };
    // Style preview should prefer this turn's shortlist when the vibe skill itself
    // has no files (e.g. select+vibe in one turn before curation is reloaded).
    if (action === "reload_vibe" && turnFiles.length > 0) {
      const existing = Array.isArray(meta.files) ? meta.files : [];
      if (existing.length === 0) meta.files = turnFiles;
    }
    window.dispatchEvent(
      new CustomEvent("luma:gallery-agent-action", {
        detail: { action, tool: call.tool, metadata: meta },
      }),
    );
  }
}

/** Drop model-invented Markdown image grids (![](DSC….jpg)) — UI CTA handles preview. */
function scrubAssistantText(text: string): string {
  const withoutImgs = text.replace(/!\[[^\]]*]\([^)]+\)\s*/g, "");
  return withoutImgs.replace(/\n{3,}/g, "\n\n").trim();
}

function previewItemsFromCall(call: AgentToolCall): ShowcasePreviewItem[] {
  const paths = showcasePathsFromCall(call);
  const files = Array.isArray(call.metadata?.files)
    ? (call.metadata.files as unknown[]).map((f) => String(f || "").trim())
    : [];
  const scores =
    call.metadata?.scores && typeof call.metadata.scores === "object"
      ? (call.metadata.scores as Record<string, number>)
      : {};
  return paths.map((path, i) => {
    const file = files[i] || path.split("/").pop() || path;
    return { path, file, score: scores[file] };
  });
}

function isShowcaseCall(call: AgentToolCall | undefined): boolean {
  return Boolean(call?.metadata?.showcase) || showcasePathsFromCall(call ?? { tool: "", args: {}, ok: false }).length > 0;
}

type OpenShowcaseFn = (
  items: ShowcasePreviewItem[],
  variant: "agent" | "vibe",
  filmLabel?: string,
  gradeClass?: string,
) => void;

function gradeClassFromVibe(sv: Record<string, unknown> | null | undefined): string | undefined {
  return typeof sv?.grade_class === "string" ? sv.grade_class : undefined;
}

function AssistantActionBar({
  calls,
  onOpenShowcase,
}: {
  calls: AgentToolCall[];
  onOpenShowcase: OpenShowcaseFn;
}) {
  const searchCall = calls.find(
    (c) =>
      c.ok &&
      c.tool === "gallery_search" &&
      String(c.metadata?.ui_action || "") === "search" &&
      Array.isArray(c.metadata?.files) &&
      (c.metadata?.files as unknown[]).length > 0,
  );
  const vibeCall = calls.find(
    (c) =>
      c.ok &&
      String(c.metadata?.ui_action || "") === "reload_vibe" &&
      c.metadata?.session_vibe &&
      typeof c.metadata.session_vibe === "object" &&
      Boolean((c.metadata.session_vibe as Record<string, unknown>).film_variant),
  );
  if (!searchCall && !vibeCall) return null;

  const emitGallery = (call: AgentToolCall, action: string) => {
    const meta = { ...(call.metadata ?? {}) };
    if (action === "reload_vibe") {
      const turnFiles = filesFromToolCalls(calls);
      if (turnFiles.length > 0 && (!Array.isArray(meta.files) || meta.files.length === 0)) {
        meta.files = turnFiles;
      }
    }
    window.dispatchEvent(
      new CustomEvent("luma:gallery-agent-action", {
        detail: { action, tool: call.tool, metadata: meta },
      }),
    );
  };

  const openSearch = () => {
    if (!searchCall) return;
    if (isShowcaseCall(searchCall)) {
      onOpenShowcase(previewItemsFromCall(searchCall), "agent");
      return;
    }
    emitGallery(searchCall, "search");
  };

  const openVibe = () => {
    if (!vibeCall) return;
    if (isShowcaseCall(vibeCall) || isShowcaseCall(searchCall)) {
      const src = isShowcaseCall(vibeCall) ? vibeCall : searchCall!;
      const sv = vibeCall.metadata?.session_vibe as Record<string, unknown> | undefined;
      const label = typeof sv?.label_zh === "string" ? sv.label_zh : undefined;
      // Prefer vibe paths; fall back to search paths from the same turn.
      const items = previewItemsFromCall(src).length
        ? previewItemsFromCall(src)
        : searchCall
          ? previewItemsFromCall(searchCall)
          : [];
      onOpenShowcase(items, "vibe", label, gradeClassFromVibe(sv));
      return;
    }
    emitGallery(vibeCall, "reload_vibe");
  };

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {searchCall ? (
        <button
          type="button"
          onClick={openSearch}
          className="rounded-[5px] border border-emerald-400/35 bg-emerald-400/15 px-2.5 py-1.5 text-[12px] font-medium text-emerald-100/95 transition-colors hover:bg-emerald-400/25"
        >
          打开预览
          {Array.isArray(searchCall.metadata?.files) ? (
            <span className="ml-1 tabular-nums text-emerald-100/55">
              {(searchCall.metadata.files as unknown[]).length}
            </span>
          ) : null}
        </button>
      ) : null}
      {vibeCall ? (
        <button
          type="button"
          onClick={openVibe}
          className="rounded-[5px] border border-amber-400/35 bg-amber-400/15 px-2.5 py-1.5 text-[12px] font-medium text-amber-100/95 transition-colors hover:bg-amber-400/25"
        >
          打开风格预览
        </button>
      ) : null}
    </div>
  );
}

const SUGGESTIONS: Record<AgentMode, string[]> = {
  gallery: [
    "帮我从这场里选出 20 张能交片的",
    "找出吉他手弹琴的特写",
    "修成梦核式修图预览看看",
  ],
  general: [
    "搜索 KEDA 的最新版本并总结它的用途",
    "用 Python 算出前 20 个斐波那契数并求和",
    "调研 SSE 与 WebSocket 的区别，写成一份 markdown 报告",
  ],
};

type PromptPhase = "select" | "style" | "find";

type PromptStages = {
  select: readonly string[];
  style: readonly string[];
  find: readonly string[];
};

const STUDIO_PROMPT_PHASE_KEY = "luma.studio_agent_prompt_phase";

function readStoredPromptPhase(): PromptPhase {
  try {
    const v = sessionStorage.getItem(STUDIO_PROMPT_PHASE_KEY);
    if (v === "style" || v === "find" || v === "select") return v;
    return "select";
  } catch {
    return "select";
  }
}

function writeStoredPromptPhase(phase: PromptPhase) {
  try {
    sessionStorage.setItem(STUDIO_PROMPT_PHASE_KEY, phase);
  } catch {
    /* ignore */
  }
}

/** First successful curation search advances Studio from select → style prompts. */
function callsCompleteSelectPhase(calls: AgentToolCall[] | undefined): boolean {
  return Boolean(
    calls?.some(
      (c) =>
        c.ok &&
        c.tool === "gallery_search" &&
        String(c.metadata?.ui_action || "") === "search",
    ),
  );
}

/** Film / dreamcore vibe advances Studio from style → find prompts. */
function callsCompleteStylePhase(calls: AgentToolCall[] | undefined): boolean {
  return Boolean(
    calls?.some((c) => {
      if (!c.ok || String(c.metadata?.ui_action || "") !== "reload_vibe") return false;
      const sv = c.metadata?.session_vibe;
      return Boolean(sv && typeof sv === "object" && (sv as Record<string, unknown>).film_variant);
    }),
  );
}

function promptsForPhase(stages: PromptStages, phase: PromptPhase): readonly string[] {
  if (phase === "find") return stages.find;
  if (phase === "style") return stages.style;
  return stages.select;
}

function phaseStepLabel(phase: PromptPhase): string {
  if (phase === "find") return "Step 3 · 再按主体找几张";
  if (phase === "style") return "Step 2 · 试试修成一种风格";
  return "Step 1 · 先选一批照片";
}

function phaseChipEyebrow(phase: PromptPhase): string {
  if (phase === "find") return "常见检索";
  if (phase === "style") return "可选风格";
  return "常见选片";
}

function phaseFollowUpEyebrow(phase: PromptPhase): string {
  if (phase === "find") return "风格好了？再试试找出主体";
  if (phase === "style") return "选好了？试试修成一种风格";
  return "还可以这样选";
}

function PromptChipList({
  prompts,
  onPick,
  eyebrow,
}: {
  prompts: readonly string[];
  onPick: (prompt: string) => void;
  eyebrow?: string;
}) {
  if (!prompts.length) return null;
  return (
    <div className="space-y-1.5">
      {eyebrow ? (
        <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/30">{eyebrow}</p>
      ) : null}
      <div className="flex flex-col gap-1.5">
        {prompts.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-[4px] border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-left text-[12px] text-white/55 transition-colors hover:border-amber-400/25 hover:bg-amber-400/[0.06] hover:text-white/80"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

const ROTATE_MS = 3200;

/** Empty-state rotating prompt (landing / Studio showcase copy). */
function RotatingPromptStage({
  prompts,
  onPick,
}: {
  prompts: readonly string[];
  onPick: (prompt: string) => void;
}) {
  const [index, setIndex] = useState(0);
  const [fade, setFade] = useState(true);
  const active = prompts[index % Math.max(prompts.length, 1)] ?? "";

  useEffect(() => {
    if (prompts.length <= 1) return;
    let fadeTimer = 0;
    const id = window.setInterval(() => {
      setFade(false);
      fadeTimer = window.setTimeout(() => {
        setIndex((i) => (i + 1) % prompts.length);
        setFade(true);
      }, 220);
    }, ROTATE_MS);
    return () => {
      window.clearInterval(id);
      window.clearTimeout(fadeTimer);
    };
  }, [prompts]);

  if (!active) return null;

  return (
    <button
      type="button"
      onClick={() => onPick(active)}
      className="group w-full rounded-[6px] border border-white/[0.08] bg-white/[0.03] px-3 py-3 text-left transition-colors hover:border-emerald-400/30 hover:bg-emerald-400/[0.06]"
    >
      <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/30 group-hover:text-emerald-200/50">
        试试这样问
      </p>
      <p
        className={[
          "mt-2 min-h-[2.75rem] text-[13px] leading-relaxed text-white/70 transition-opacity duration-200",
          fade ? "opacity-100" : "opacity-0",
        ].join(" ")}
      >
        {active}
      </p>
      <p className="mt-2 font-mono text-[10px] text-white/28 group-hover:text-emerald-200/55">点击发送 →</p>
    </button>
  );
}

const MODE_LABEL: Record<AgentMode, string> = {
  gallery: "策展助手",
  general: "通用助手",
};

const MODE_HINT: Record<AgentMode, string> = {
  gallery:
    "我可以基于当前 session 的分析结果回答关于评分、标签、保留/丢弃的问题（只读，不会改动文件）。",
  general:
    "我可以联网检索、读取网页、运行沙箱 Python、并把结果保存为可下载的产物。",
};

/** Render assistant text with clickable http(s) links (web results / artifacts). */
function LinkifiedText({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s，。、）)]+)/g);
  return (
    <>
      {parts.map((part, i) =>
        /^https?:\/\//.test(part) ? (
          <a
            key={i}
            href={part}
            target="_blank"
            rel="noreferrer noopener"
            className="text-sky-300/90 underline decoration-sky-300/40 underline-offset-2 hover:text-sky-200"
          >
            {part}
          </a>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function showcasePathsFromCall(call: AgentToolCall): string[] {
  const paths = Array.isArray(call.metadata?.paths)
    ? (call.metadata.paths as unknown[]).map((p) => String(p || "").trim()).filter(Boolean)
    : [];
  return paths.filter((p) => p.startsWith("/showcase/") || p.startsWith("/demo/"));
}

function ShowcaseThumbStrip({
  paths,
  scores,
  onOpen,
}: {
  paths: string[];
  scores?: Record<string, number>;
  onOpen?: () => void;
}) {
  if (!paths.length) return null;
  return (
    <div className="mt-2 grid grid-cols-3 gap-1.5 sm:grid-cols-4">
      {paths.map((src) => {
        const file = src.split("/").pop() || src;
        const score = scores?.[file];
        const className =
          "group relative block overflow-hidden rounded-[4px] border border-white/[0.08] bg-black/40 text-left";
        const body = (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt="" className="aspect-[4/3] h-full w-full object-cover transition-opacity group-hover:opacity-90" />
            {score != null ? (
              <span className="absolute bottom-1 right-1 rounded-[2px] bg-black/65 px-1 py-0.5 font-mono text-[9px] tabular-nums text-white/80">
                {Number(score).toFixed(1)}
              </span>
            ) : null}
          </>
        );
        if (onOpen) {
          return (
            <button key={src} type="button" onClick={onOpen} className={className}>
              {body}
            </button>
          );
        }
        return (
          <a key={src} href={src} target="_blank" rel="noreferrer" className={className}>
            {body}
          </a>
        );
      })}
    </div>
  );
}

function ToolChip({
  call,
  onOpenShowcase,
}: {
  call: AgentToolCall;
  onOpenShowcase?: OpenShowcaseFn;
}) {
  const argStr = useMemo(() => {
    try {
      const s = JSON.stringify(call.args ?? {});
      return s === "{}" ? "" : s;
    } catch {
      return "";
    }
  }, [call.args]);
  const files = Array.isArray(call.metadata?.files)
    ? (call.metadata.files as unknown[]).map((f) => String(f || "").trim()).filter(Boolean)
    : [];
  const showcasePaths = showcasePathsFromCall(call);
  const scores =
    call.metadata?.scores && typeof call.metadata.scores === "object"
      ? (call.metadata.scores as Record<string, number>)
      : undefined;
  const uiAction = String(call.metadata?.ui_action || "");
  const showcase = isShowcaseCall(call);
  const canPreviewSearch =
    call.ok &&
    call.tool === "gallery_search" &&
    uiAction === "search" &&
    files.length > 0;
  const vibeMeta =
    call.metadata?.session_vibe && typeof call.metadata.session_vibe === "object"
      ? (call.metadata.session_vibe as Record<string, unknown>)
      : null;
  const canPreviewVibe =
    call.ok &&
    uiAction === "reload_vibe" &&
    Boolean(vibeMeta?.film_variant) &&
    (call.tool === "apply_film_vibe" || Boolean(vibeMeta));

  const openSearch = () => {
    if (showcase && onOpenShowcase) {
      onOpenShowcase(previewItemsFromCall(call), "agent");
      return;
    }
    window.dispatchEvent(
      new CustomEvent("luma:gallery-agent-action", {
        detail: { action: "search", tool: call.tool, metadata: call.metadata ?? {} },
      }),
    );
  };

  const openVibe = () => {
    if (showcase && onOpenShowcase) {
      const label = typeof vibeMeta?.label_zh === "string" ? vibeMeta.label_zh : undefined;
      onOpenShowcase(previewItemsFromCall(call), "vibe", label, gradeClassFromVibe(vibeMeta));
      return;
    }
    window.dispatchEvent(
      new CustomEvent("luma:gallery-agent-action", {
        detail: { action: "reload_vibe", tool: call.tool, metadata: call.metadata ?? {} },
      }),
    );
  };

  return (
    <span className="flex w-full min-w-[12rem] flex-col gap-1.5">
      <span className="inline-flex flex-wrap items-center gap-1">
        <span
          title={argStr ? `args: ${argStr}` : undefined}
          className="inline-flex items-center gap-1 rounded-[3px] border border-white/[0.08] bg-white/[0.04] px-1.5 py-0.5 text-[11px] text-white/55"
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${call.ok ? "bg-emerald-400/90" : "bg-rose-400/90"}`}
            aria-hidden
          />
          <span className="font-mono">{call.tool}</span>
          {files.length > 0 ? (
            <span className="tabular-nums text-white/35">{files.length}</span>
          ) : null}
        </span>
        {canPreviewSearch ? (
          <button
            type="button"
            onClick={openSearch}
            className="rounded-[3px] border border-emerald-400/25 bg-emerald-400/[0.08] px-1.5 py-0.5 text-[11px] text-emerald-200/85 transition-colors hover:bg-emerald-400/[0.14]"
          >
            打开预览
          </button>
        ) : null}
        {canPreviewVibe ? (
          <button
            type="button"
            onClick={openVibe}
            className="rounded-[3px] border border-amber-400/25 bg-amber-400/[0.08] px-1.5 py-0.5 text-[11px] text-amber-200/85 transition-colors hover:bg-amber-400/[0.14]"
          >
            打开风格预览
          </button>
        ) : null}
      </span>
      {showcasePaths.length ? (
        <ShowcaseThumbStrip
          paths={showcasePaths}
          scores={scores}
          onOpen={
            onOpenShowcase
              ? () =>
                  onOpenShowcase(
                    previewItemsFromCall(call),
                    canPreviewVibe ? "vibe" : "agent",
                    typeof vibeMeta?.label_zh === "string" ? vibeMeta.label_zh : undefined,
                    canPreviewVibe ? gradeClassFromVibe(vibeMeta) : undefined,
                  )
              : undefined
          }
        />
      ) : null}
    </span>
  );
}

function GuardrailChip({ ev }: { ev: AgentGuardrailEvent }) {
  return (
    <span
      title={ev.matches?.length ? ev.matches.join(", ") : ev.kind}
      className="inline-flex items-center gap-1 rounded-[3px] border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-100/80"
    >
      <span aria-hidden className="font-mono text-[10px] text-amber-300/80">
        !
      </span>
      <span className="font-mono">{ev.kind}</span>
    </span>
  );
}

function AuthPanel({
  onSubmit,
  onClose,
}: {
  onSubmit: (kind: "login" | "register", username: string, password: string) => Promise<void>;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(async () => {
    if (busy) return;
    setErr(null);
    setBusy(true);
    try {
      await onSubmit(kind, username.trim(), password);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }, [busy, kind, username, password, onSubmit]);

  return (
    <div className="absolute inset-0 z-10 flex flex-col bg-[#0d0d0d]/98 p-4 backdrop-blur-sm">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[13px] font-medium text-white/80">
          {kind === "login" ? "登录" : "注册"}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-[3px] px-1.5 py-0.5 text-[14px] text-white/40 hover:text-white/70"
          aria-label="关闭"
        >
          ×
        </button>
      </div>
      <p className="mb-3 text-[11px] leading-relaxed text-white/35">
        登录后对话记忆会按账号持久保存、跨设备与刷新恢复；匿名使用则仅在本浏览器会话内保留。
      </p>
      <div className="flex flex-col gap-2">
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名（3-32 位）"
          autoComplete="username"
          className="rounded-[5px] border border-white/[0.08] bg-white/[0.04] px-2.5 py-2 text-[13px] text-white/80 placeholder:text-white/28 focus:border-white/[0.14] focus:outline-none"
        />
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
          }}
          type="password"
          placeholder="密码（≥6 位）"
          autoComplete={kind === "login" ? "current-password" : "new-password"}
          className="rounded-[5px] border border-white/[0.08] bg-white/[0.04] px-2.5 py-2 text-[13px] text-white/80 placeholder:text-white/28 focus:border-white/[0.14] focus:outline-none"
        />
        {err ? <p className="text-[11px] text-rose-300/85">{err}</p> : null}
        <button
          type="button"
          disabled={busy || !username.trim() || !password}
          onClick={() => void submit()}
          className="mt-1 rounded-[5px] border border-white/[0.1] bg-white/[0.08] px-3 py-2 text-[13px] text-white/80 transition-colors hover:bg-white/[0.14] disabled:opacity-35"
        >
          {busy ? "提交中…" : kind === "login" ? "登录" : "注册并登录"}
        </button>
        <button
          type="button"
          onClick={() => {
            setKind((k) => (k === "login" ? "register" : "login"));
            setErr(null);
          }}
          className="text-[11px] text-white/40 hover:text-white/65"
        >
          {kind === "login" ? "没有账号？去注册" : "已有账号？去登录"}
        </button>
      </div>
    </div>
  );
}

export function ChatDock({
  apiBase,
  previewsDir,
  context = "gallery",
  initialPrompt,
  defaultOpen = false,
  rotatingPrompts,
  promptStages,
}: {
  apiBase: string;
  previewsDir?: string | null;
  context?: string;
  /** Prefill + open dock once (e.g. landing hero `?q=`), then auto-send. */
  initialPrompt?: string | null;
  /** Open the panel on mount (Studio entry). */
  defaultOpen?: boolean;
  /** When set, empty state scrolls these prompts (click to send). */
  rotatingPrompts?: readonly string[];
  /**
   * Studio three-step prompts: select → style → find (e.g. 吉他手).
   * Phase advances on tool success; persisted in sessionStorage.
   */
  promptStages?: PromptStages;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<AgentMode>("gallery");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [promptPhase, setPromptPhase] = useState<PromptPhase>(() =>
    promptStages ? readStoredPromptPhase() : "select",
  );
  const [showcasePreview, setShowcasePreview] = useState<{
    items: ShowcasePreviewItem[];
    variant: "agent" | "vibe";
    filmLabel?: string;
    gradeClass?: string;
  } | null>(null);
  const sessionIdRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const consumedInitialPrompt = useRef(false);
  const pendingAutoSend = useRef<string | null>(null);
  const sendRef = useRef<(raw: string) => Promise<void>>(async () => {});

  const openShowcasePreview = useCallback<OpenShowcaseFn>(
    (items, variant, filmLabel, gradeClass) => {
      if (!items.length) return;
      // Keep the curator dock on top of the preview so the user can keep chatting.
      setOpen(true);
      setShowcasePreview({ items, variant, filmLabel, gradeClass });
    },
    [],
  );

  const advancePromptPhase = useCallback(
    (calls?: AgentToolCall[]) => {
      if (!promptStages) return;
      let next: PromptPhase = promptPhase;
      // Cascade so a single style turn (search + vibe) can skip select → find.
      if (next === "select" && callsCompleteSelectPhase(calls)) next = "style";
      if (next === "style" && callsCompleteStylePhase(calls)) next = "find";
      if (next === promptPhase) return;
      setPromptPhase(next);
      writeStoredPromptPhase(next);
    },
    [promptStages, promptPhase],
  );

  const stagePrompts = promptStages ? promptsForPhase(promptStages, promptPhase) : null;
  const emptyRotatingPrompts = stagePrompts ?? rotatingPrompts;

  // Studio / Showcase: Gallery page is not mounted, so auto-open the static preview
  // when Agent emits the same gallery UI actions with /showcase paths.
  useEffect(() => {
    const onAgentAction = (ev: Event) => {
      const detail = (ev as CustomEvent).detail as {
        action?: string;
        metadata?: Record<string, unknown>;
      } | null;
      const meta = detail?.metadata;
      if (!meta) return;
      const fakeCall: AgentToolCall = { tool: "gallery_search", args: {}, ok: true, metadata: meta };
      const items = previewItemsFromCall(fakeCall);
      if (!items.length) return;
      const action = String(detail?.action || "");
      const sv =
        meta.session_vibe && typeof meta.session_vibe === "object"
          ? (meta.session_vibe as Record<string, unknown>)
          : null;
      if (action === "reload_vibe" && sv?.film_variant) {
        openShowcasePreview(
          items,
          "vibe",
          typeof sv.label_zh === "string" ? sv.label_zh : undefined,
          gradeClassFromVibe(sv),
        );
        return;
      }
      if (action === "search") {
        openShowcasePreview(items, "agent");
      }
    };
    window.addEventListener("luma:gallery-agent-action", onAgentAction as EventListener);
    return () => window.removeEventListener("luma:gallery-agent-action", onAgentAction as EventListener);
  }, [openShowcasePreview]);

  if (!sessionIdRef.current) {
    sessionIdRef.current = persistentSessionId(context, mode);
  }

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, open, sending]);

  // Reflect stored session immediately, then validate the token against the server.
  useEffect(() => {
    setUser(getStoredUser());
    void fetchMe(apiBase).then(setUser).catch(() => {});
  }, [apiBase]);

  useEffect(() => {
    const prompt = initialPrompt?.trim();
    if (!prompt || consumedInitialPrompt.current) return;
    consumedInitialPrompt.current = true;
    pendingAutoSend.current = prompt;
    setInput(prompt);
    setOpen(true);
  }, [initialPrompt]);

  // Restore the persisted transcript when opening, switching mode, or auth changes.
  useEffect(() => {
    if (!open) return;
    const sid = persistentSessionId(context, mode);
    sessionIdRef.current = sid;
    let cancelled = false;
    void fetchAgentHistory(apiBase, sid, mode).then((hist) => {
      if (cancelled) return;
      setTurns(hist.map((h) => ({ role: h.role, text: h.text })));
      const pending = pendingAutoSend.current;
      if (pending) {
        pendingAutoSend.current = null;
        // After history hydrate, send the landing hero prompt as a real turn.
        window.setTimeout(() => {
          void sendRef.current(pending);
        }, 50);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [open, mode, user, apiBase, context]);

  // Mutate the trailing assistant turn (the one currently streaming) in place.
  const patchLastAssistant = useCallback(
    (patch: (t: ChatTurn) => ChatTurn) => {
      setTurns((prev) => {
        if (prev.length === 0) return prev;
        const idx = prev.length - 1;
        if (prev[idx].role !== "assistant") return prev;
        const next = prev.slice();
        next[idx] = patch(next[idx]);
        return next;
      });
    },
    [],
  );

  const send = useCallback(
    async (raw: string) => {
      const message = raw.trim();
      if (!message || sending) return;
      setInput("");
      // Push the user turn + an empty assistant placeholder we stream into.
      setTurns((prev) => [
        ...prev,
        { role: "user", text: message },
        { role: "assistant", text: "", toolCalls: [], streaming: true },
      ]);
      setSending(true);

      const body = {
        session_id: sessionIdRef.current,
        message,
        mode,
        previews_dir: mode === "gallery" ? previewsDir ?? undefined : undefined,
      };

      try {
        const emittedUiTools = new Set<string>();
        const turnToolCalls: AgentToolCall[] = [];
        const emitOnce = (calls: AgentToolCall[]) => {
          const fresh: AgentToolCall[] = [];
          for (const c of calls) {
            if (!c?.ok || !c.metadata?.ui_action) continue;
            const key = `${c.tool}:${String(c.metadata?.ui_action || "")}:${JSON.stringify(c.args ?? {})}`;
            if (emittedUiTools.has(key)) continue;
            emittedUiTools.add(key);
            fresh.push(c);
          }
          if (fresh.length === 0) return;
          // Emit only fresh actions; pass whole turn so vibe can inherit search/select files.
          emitGalleryUiActions(fresh, turnToolCalls.length ? turnToolCalls : fresh);
        };
        const { receivedToken } = await streamAgentChat(apiBase, body, {
          onToken: (text) =>
            patchLastAssistant((t) => ({ ...t, text: t.text + text })),
          onToolCall: (call) => {
            turnToolCalls.push(call);
            patchLastAssistant((t) => ({ ...t, toolCalls: [...(t.toolCalls ?? []), call] }));
            // Open Gallery preview as soon as the write skill returns — don't wait for the
            // model's final prose (which often claims success without a CTA).
            emitOnce([call]);
          },
          onDone: (info) => {
            const calls = info.tool_calls?.length ? info.tool_calls : undefined;
            if (calls?.length) {
              turnToolCalls.length = 0;
              turnToolCalls.push(...calls);
            }
            patchLastAssistant((t) => ({
              ...t,
              text: (t.text || info.reply || "(空回复)").trim(),
              toolCalls: calls ?? t.toolCalls,
              guardrails: info.guardrail_events,
              streaming: false,
            }));
            const doneCalls = calls ?? turnToolCalls;
            emitOnce(doneCalls);
            advancePromptPhase(doneCalls);
          },
          onError: (msg) =>
            patchLastAssistant((t) => ({ ...t, text: msg, error: true, streaming: false })),
        });

        // SSE opened but yielded no content (e.g. proxy buffering) → non-stream fallback.
        if (!receivedToken) {
          const data = await sendAgentChat(apiBase, body);
          patchLastAssistant((t) => ({
            ...t,
            text: data.error || data.reply || "(空回复)",
            toolCalls: data.tool_calls,
            guardrails: data.guardrail_events,
            error: Boolean(data.error),
            streaming: false,
          }));
          emitGalleryUiActions(data.tool_calls ?? []);
          if (!data.error) advancePromptPhase(data.tool_calls);
        }
      } catch (streamErr) {
        // Hard stream failure → fall back to the non-streaming endpoint once.
        try {
          const data = await sendAgentChat(apiBase, body);
          patchLastAssistant((t) => ({
            ...t,
            text: data.error || data.reply || "(空回复)",
            toolCalls: data.tool_calls,
            guardrails: data.guardrail_events,
            error: Boolean(data.error),
            streaming: false,
          }));
          emitGalleryUiActions(data.tool_calls ?? []);
          if (!data.error) advancePromptPhase(data.tool_calls);
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : streamErr instanceof Error ? streamErr.message : "请求失败";
          patchLastAssistant((t) => ({ ...t, text: msg, error: true, streaming: false }));
        }
      } finally {
        setSending(false);
      }
    },
    [apiBase, previewsDir, sending, mode, patchLastAssistant, advancePromptPhase],
  );

  sendRef.current = send;

  const resetChat = useCallback(() => {
    setTurns([]);
    // Rotate to a brand-new persisted conversation for this mode.
    sessionIdRef.current = rotateSessionId(context, mode);
    if (promptStages) {
      setPromptPhase("select");
      writeStoredPromptPhase("select");
    }
  }, [context, mode, promptStages]);

  const switchMode = useCallback(
    (next: AgentMode) => {
      if (next === mode) return;
      setMode(next);
      // The hydrate effect (keyed on mode) restores that mode's persisted transcript.
      sessionIdRef.current = persistentSessionId(context, next);
    },
    [mode, context],
  );

  const doAuth = useCallback(
    async (kind: "login" | "register", username: string, password: string) => {
      const u =
        kind === "login"
          ? await loginUser(apiBase, username, password)
          : await registerUser(apiBase, username, password);
      setUser(u);
      setAuthOpen(false);
    },
    [apiBase],
  );

  const doLogout = useCallback(async () => {
    await logoutUser(apiBase);
    setUser(null);
  }, [apiBase]);

  return (
    <>
    {showcasePreview ? (
      <ShowcasePreviewModal
        items={showcasePreview.items}
        variant={showcasePreview.variant}
        filmLabel={showcasePreview.filmLabel}
        gradeClass={showcasePreview.gradeClass}
        onClose={() => setShowcasePreview(null)}
      />
    ) : null}

    {/* z-70: above ShowcasePreviewModal (60) / SelectedPreviewModal (55). */}
    <div
      data-chat-dock
      className="fixed right-4 z-[70] flex flex-col items-end gap-2 transition-[bottom] duration-200"
      style={{ bottom: "var(--luma-chat-bottom, 1rem)" }}
    >
      {open ? (
        <div className="flex h-[min(560px,calc(100vh-5.5rem-var(--luma-chat-bottom,1rem)))] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-[8px] border border-white/[0.1] bg-[#0d0d0d]/95 shadow-2xl backdrop-blur-md">
          {authOpen ? <AuthPanel onSubmit={doAuth} onClose={() => setAuthOpen(false)} /> : null}
          <div className="flex shrink-0 items-center justify-between border-b border-white/[0.06] px-3 py-2.5">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-emerald-400/90 shadow-[0_0_10px_rgba(52,211,153,0.5)]" aria-hidden />
              <div className="flex items-center rounded-[5px] border border-white/[0.08] bg-white/[0.03] p-0.5 text-[12px]">
                {(["gallery", "general"] as AgentMode[]).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => switchMode(m)}
                    className={[
                      "rounded-[4px] px-2 py-0.5 transition-colors",
                      mode === m ? "bg-white/[0.12] text-white/85" : "text-white/40 hover:text-white/70",
                    ].join(" ")}
                  >
                    {MODE_LABEL[m]}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-1">
              {user ? (
                <button
                  type="button"
                  onClick={() => void doLogout()}
                  title={`已登录：${user.username}（点击退出）`}
                  className="max-w-[92px] truncate rounded-[3px] px-1.5 py-0.5 text-[12px] text-emerald-300/70 hover:text-emerald-200"
                >
                  {user.username}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setAuthOpen(true)}
                  title="登录以持久保存对话"
                  className="rounded-[3px] px-1.5 py-0.5 text-[12px] text-white/35 hover:text-white/60"
                >
                  登录
                </button>
              )}
              <button
                type="button"
                onClick={resetChat}
                title="清空对话"
                className="rounded-[3px] px-1.5 py-0.5 text-[12px] text-white/35 hover:text-white/60"
              >
                清空
              </button>
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
            {turns.length === 0 ? (
              <div className="space-y-3 pt-2">
                <p className="text-[12px] leading-relaxed text-white/35">{MODE_HINT[mode]}</p>
                {promptStages ? (
                  <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/28">
                    {phaseStepLabel(promptPhase)}
                  </p>
                ) : null}
                {emptyRotatingPrompts && emptyRotatingPrompts.length > 0 ? (
                  <RotatingPromptStage
                    key={promptPhase}
                    prompts={emptyRotatingPrompts}
                    onPick={(p) => void send(p)}
                  />
                ) : null}
                {promptStages ? (
                  <PromptChipList
                    prompts={
                      promptPhase === "select"
                        ? promptStages.select.slice(0, 4)
                        : promptsForPhase(promptStages, promptPhase)
                    }
                    onPick={(p) => void send(p)}
                    eyebrow={phaseChipEyebrow(promptPhase)}
                  />
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {SUGGESTIONS[mode].map((s) => (
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
                )}
              </div>
            ) : (
              <>
              {turns.map((t, i) => (
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
                    {t.streaming && !t.text && !t.toolCalls?.length ? (
                      <div className="flex items-center gap-1 text-white/40">
                        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-white/40" />
                        思考中…
                      </div>
                    ) : (
                      <div>
                        {t.role === "assistant" ? (
                          <LinkifiedText text={scrubAssistantText(t.text)} />
                        ) : (
                          t.text
                        )}
                        {t.streaming ? (
                          <span className="ml-0.5 inline-block h-[1em] w-[2px] animate-pulse bg-white/50 align-[-0.15em]" aria-hidden />
                        ) : null}
                      </div>
                    )}
                    {t.role === "assistant" && t.toolCalls?.length ? (
                      <AssistantActionBar calls={t.toolCalls} onOpenShowcase={openShowcasePreview} />
                    ) : null}
                    {(t.toolCalls?.length || t.guardrails?.length) ? (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {t.toolCalls?.map((c, j) => (
                          <ToolChip key={`t${j}`} call={c} onOpenShowcase={openShowcasePreview} />
                        ))}
                        {t.guardrails?.map((g, j) => <GuardrailChip key={`g${j}`} ev={g} />)}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
              {promptStages && promptPhase !== "select" && !sending ? (
                <div className="rounded-[6px] border border-amber-400/20 bg-amber-400/[0.04] px-2.5 py-2.5">
                  <PromptChipList
                    prompts={promptsForPhase(promptStages, promptPhase)}
                    onPick={(p) => void send(p)}
                    eyebrow={phaseFollowUpEyebrow(promptPhase)}
                  />
                </div>
              ) : null}
              </>
            )}
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
                placeholder={mode === "gallery" ? "问问这个 session 的照片…" : "给我一个任务：检索、算一算、或生成一份产物…"}
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
      ) : null}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "收起策展助手" : "打开策展助手"}
        aria-expanded={open}
        title={open ? "收起策展助手" : "策展助手"}
        className={[
          "flex h-12 w-12 items-center justify-center rounded-[14px] border shadow-lg backdrop-blur-md transition-colors",
          open
            ? "border-emerald-400/35 bg-emerald-400/15 text-emerald-100 hover:bg-emerald-400/25"
            : "border-white/[0.1] bg-white/[0.08] text-white/80 hover:bg-white/[0.14]",
        ].join(" ")}
      >
        {open ? (
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        ) : (
          <img
            src="/brand/luma-icon.png"
            alt=""
            width={32}
            height={32}
            className="h-8 w-8 rounded-[10px] object-cover"
            draggable={false}
          />
        )}
      </button>
    </div>
    </>
  );
}
