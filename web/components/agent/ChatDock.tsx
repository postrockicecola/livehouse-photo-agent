"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAgentHistory,
  persistentSessionId,
  readGalleryFocusContext,
  rotateSessionId,
  sendAgentChat,
  streamAgentChat,
  type AgentGuardrailEvent,
  type AgentHistoryTurn,
  type AgentMode,
  type AgentToolCall,
} from "@/components/agent/agentChat";
import {
  ShowcasePreviewModal,
  type ShowcasePreviewItem,
} from "@/components/agent/ShowcasePreviewModal";
import {
  GALLERY_RECIPE_CHIPS,
  GALLERY_SEMANTIC_SUGGESTIONS,
} from "@/lib/productIa";
import { clearSessionVibeApi, fetchSessionVibe, saveSessionVibe } from "@/lib/sessionVibe";

type ChatTurn = {
  role: "user" | "assistant";
  text: string;
  toolCalls?: AgentToolCall[];
  guardrails?: AgentGuardrailEvent[];
  error?: boolean;
  streaming?: boolean;
  streamStatus?: string;
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

function focusedFileFromCall(call: AgentToolCall): string {
  return String(
    call.args?.focus_file ||
      call.args?.file ||
      call.metadata?.focus_file ||
      "",
  ).trim();
}

function vibePreviewMetadata(call: AgentToolCall): Record<string, unknown> {
  const meta = { ...(call.metadata ?? {}) };
  const focus = focusedFileFromCall(call);
  if (!focus) return meta;
  meta.files = [focus];
  meta.focus_file = focus;
  if (Array.isArray(meta.paths)) {
    const focusedPaths = (meta.paths as unknown[]).filter((value) => {
      const path = String(value || "").trim();
      return path.split("/").pop() === focus;
    });
    meta.paths = focusedPaths;
  }
  return meta;
}

/** Notify Gallery page to reload curation / vibe after write skills. */
function emitGalleryUiActions(
  calls: AgentToolCall[],
  turnContext: AgentToolCall[] = calls,
  focusFile?: string,
) {
  if (typeof window === "undefined") return;
  const turnFiles = filesFromToolCalls(turnContext);
  for (const call of calls) {
    if (!call?.ok) continue;
    const action = String(call.metadata?.ui_action || "");
    if (!action) continue;
    const meta = { ...(call.metadata ?? {}) };
    if (action === "reload_vibe") {
      const focus = String(focusFile || "").trim();
      // The photo open when this message was sent is the strongest preview scope.
      // Do not let stale curation/tool metadata expand a focused edit to many photos.
      if (focus) {
        meta.files = [focus];
        meta.focus_file = focus;
      } else if (turnFiles.length > 0) {
        // No focused photo: inherit this turn's shortlist only when the vibe skill
        // itself did not provide an explicit preview set.
        const existing = Array.isArray(meta.files) ? meta.files : [];
        if (existing.length === 0) meta.files = turnFiles;
      }
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
  const metadata = isVibePreviewCall(call) ? vibePreviewMetadata(call) : (call.metadata ?? {});
  const scopedCall = { ...call, metadata };
  const paths = showcasePathsFromCall(scopedCall);
  const files = Array.isArray(metadata.files)
    ? (metadata.files as unknown[]).map((f) => String(f || "").trim())
    : [];
  const scores =
    metadata.scores && typeof metadata.scores === "object"
      ? (metadata.scores as Record<string, number>)
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

function isVibePreviewCall(c: AgentToolCall): boolean {
  if (!c?.ok) return false;
  const ui = String(c.metadata?.ui_action || "");
  // Cleared vibe — no preview CTA.
  if (ui === "reload_vibe" && c.metadata?.session_vibe == null) return false;
  const sv =
    c.metadata?.session_vibe && typeof c.metadata.session_vibe === "object"
      ? (c.metadata.session_vibe as Record<string, unknown>)
      : null;
  if (sv?.film_variant) {
    return (
      ui === "reload_vibe" ||
      c.tool === "apply_film_vibe" ||
      c.tool === "recommend_film_for_photo"
    );
  }
  // Partial metadata: skill ran, session vibe can be fetched on click.
  return (
    (c.tool === "apply_film_vibe" || c.tool === "recommend_film_for_photo") &&
    ui === "reload_vibe"
  );
}

function mentionsVibePreviewCta(text: string): boolean {
  return /打开风格预览/.test(text || "");
}

function mergeHistoryTurns(prev: ChatTurn[], hist: AgentHistoryTurn[]): ChatTurn[] {
  // Never clobber an in-flight stream with a text-only hydrate.
  if (prev.some((t) => t.streaming)) return prev;
  const next: ChatTurn[] = hist.map((h) => ({
    role: h.role,
    text: h.text,
    toolCalls: h.toolCalls,
  }));
  // Preserve richer local toolCalls when history row is text-only (race / old rows).
  for (let i = 0; i < next.length; i++) {
    if (next[i].role !== "assistant") continue;
    if (next[i].toolCalls?.length) continue;
    const local = prev.find(
      (p) =>
        p.role === "assistant" &&
        (p.text || "").trim() === (next[i].text || "").trim() &&
        (p.toolCalls?.length ?? 0) > 0,
    );
    if (local?.toolCalls?.length) {
      next[i] = { ...next[i], toolCalls: local.toolCalls, guardrails: local.guardrails };
    }
  }
  // Keep trailing local turns not yet visible in persisted history.
  if (prev.length > next.length) {
    return [...next, ...prev.slice(next.length)];
  }
  return next;
}

async function openSessionVibePreview(
  apiBase: string,
  fallbackPrompt?: string,
): Promise<"ok" | "missing" | "error"> {
  try {
    let data = await fetchSessionVibe(apiBase);
    let sv = data.session_vibe;
    // Model often claims success without calling apply_film_vibe — apply from the
    // user's style ask so the CTA still opens a real graded preview.
    if (!sv?.film_variant && fallbackPrompt?.trim()) {
      data = await saveSessionVibe(apiBase, fallbackPrompt.trim());
      sv = data.session_vibe;
    }
    if (!sv?.film_variant) return "missing";
    window.dispatchEvent(
      new CustomEvent("luma:gallery-agent-action", {
        detail: {
          action: "reload_vibe",
          tool: "apply_film_vibe",
          metadata: { ui_action: "reload_vibe", session_vibe: sv, files: [] },
        },
      }),
    );
    return "ok";
  } catch {
    return "error";
  }
}

function VibePreviewFallbackButton({
  apiBase,
  fallbackPrompt,
}: {
  apiBase: string;
  fallbackPrompt?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="mt-2 flex flex-col gap-1">
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setErr(null);
            void openSessionVibePreview(apiBase, fallbackPrompt)
              .then((status) => {
                if (status === "missing") setErr("未能解析风格，请换个说法再试（如：复古胶片 / 黑白纪实）");
                if (status === "error") setErr("打开失败，请稍后重试");
              })
              .finally(() => setBusy(false));
          }}
          className="rounded-[5px] border border-amber-400/35 bg-amber-400/15 px-2.5 py-1.5 text-[12px] font-medium text-amber-100/95 transition-colors hover:bg-amber-400/25 disabled:opacity-50"
        >
          {busy ? "应用并打开…" : "打开风格预览"}
        </button>
      </div>
      {err ? <p className="text-[11px] text-rose-300/80">{err}</p> : null}
    </div>
  );
}

function AssistantActionBar({
  calls,
  onOpenShowcase,
  apiBase,
}: {
  calls: AgentToolCall[];
  onOpenShowcase: OpenShowcaseFn;
  apiBase: string;
}) {
  const searchCall = calls.find(
    (c) =>
      c.ok &&
      c.tool === "gallery_search" &&
      String(c.metadata?.ui_action || "") === "search" &&
      Array.isArray(c.metadata?.files) &&
      (c.metadata?.files as unknown[]).length > 0,
  );
  const vibeCall = calls.find(isVibePreviewCall);
  if (!searchCall && !vibeCall) return null;

  const emitGallery = (call: AgentToolCall, action: string) => {
    const meta =
      action === "reload_vibe"
        ? vibePreviewMetadata(call)
        : { ...(call.metadata ?? {}) };
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

  const [vibeErr, setVibeErr] = useState<string | null>(null);
  const [vibeBusy, setVibeBusy] = useState(false);

  const openVibe = () => {
    if (!vibeCall || vibeBusy) return;
    setVibeErr(null);
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
      if (items.length) {
        onOpenShowcase(items, "vibe", label, gradeClassFromVibe(sv));
        return;
      }
    }
    const sv = vibeCall.metadata?.session_vibe;
    if (sv && typeof sv === "object" && (sv as Record<string, unknown>).film_variant) {
      emitGallery(vibeCall, "reload_vibe");
      return;
    }
    setVibeBusy(true);
    const promptFromArgs =
      typeof vibeCall.args?.prompt === "string" ? String(vibeCall.args.prompt) : "";
    void openSessionVibePreview(apiBase, promptFromArgs)
      .then((status) => {
        if (status === "missing") setVibeErr("未能解析风格，请换个说法再试（如：复古胶片 / 黑白纪实）");
        if (status === "error") setVibeErr("打开失败，请稍后重试");
      })
      .finally(() => setVibeBusy(false));
  };

  return (
    <div className="mt-2 flex flex-col gap-1">
      <div className="flex flex-wrap gap-1.5">
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
            disabled={vibeBusy}
            onClick={openVibe}
            className="rounded-[5px] border border-amber-400/35 bg-amber-400/15 px-2.5 py-1.5 text-[12px] font-medium text-amber-100/95 transition-colors hover:bg-amber-400/25 disabled:opacity-50"
          >
            {vibeBusy ? "打开中…" : "打开风格预览"}
          </button>
        ) : null}
      </div>
      {vibeErr ? <p className="text-[11px] text-rose-300/80">{vibeErr}</p> : null}
    </div>
  );
}

type PromptPhase = "select" | "style" | "find";

function RecipeChipRow({
  onPick,
  disabled,
}: {
  onPick: (prompt: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {GALLERY_RECIPE_CHIPS.map((chip) => (
        <button
          key={chip.id}
          type="button"
          disabled={disabled}
          title={chip.prompt}
          onClick={() => onPick(chip.prompt)}
          className="rounded-[4px] border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[11px] text-white/55 transition-colors hover:border-emerald-400/30 hover:bg-emerald-400/[0.08] hover:text-white/80 disabled:opacity-35"
        >
          {chip.label}
        </button>
      ))}
    </div>
  );
}

function FocusActionBar({
  focusFile,
  onPick,
  disabled,
}: {
  focusFile: string;
  onPick: (prompt: string) => void;
  disabled?: boolean;
}) {
  const short = focusFile.length > 28 ? `${focusFile.slice(0, 12)}…${focusFile.slice(-10)}` : focusFile;
  return (
    <div className="rounded-[6px] border border-sky-400/20 bg-sky-400/[0.05] px-2.5 py-2">
      <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-sky-200/45">
        焦点图 · {short}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <button
          type="button"
          disabled={disabled}
          onClick={() => onPick(`解释一下这张照片（${focusFile}）`)}
          className="rounded-[4px] border border-sky-400/25 bg-sky-400/[0.08] px-2 py-1 text-[11px] text-sky-100/80 transition-colors hover:bg-sky-400/[0.14] disabled:opacity-35"
        >
          解释这张
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onPick("最适合这张的胶片感")}
          className="rounded-[4px] border border-amber-400/25 bg-amber-400/[0.08] px-2 py-1 text-[11px] text-amber-100/80 transition-colors hover:bg-amber-400/[0.14] disabled:opacity-35"
        >
          推荐胶片
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onPick("找出技术高但构图一般的照片")}
          className="rounded-[4px] border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[11px] text-white/55 transition-colors hover:bg-white/[0.07] hover:text-white/75 disabled:opacity-35"
        >
          技术 vs 构图
        </button>
      </div>
    </div>
  );
}

/** Honest next-step chips when gallery_search returned zero hits. */
function SearchMissHints({
  calls,
  onPick,
  disabled,
}: {
  calls: AgentToolCall[];
  onPick: (prompt: string) => void;
  disabled?: boolean;
}) {
  const miss = calls.find((c) => {
    if (!c.ok || c.tool !== "gallery_search") return false;
    // Prefer explicit count=0; also treat empty files on a search ui_action as a miss.
    const count = Number(c.metadata?.count ?? NaN);
    if (count === 0) return true;
    const files = Array.isArray(c.metadata?.files) ? c.metadata.files : null;
    return String(c.metadata?.ui_action || "") === "search" && files !== null && files.length === 0;
  });
  if (!miss) return null;

  const tagStatus = String(miss.metadata?.tag_status || "");
  const styleIntent = String(miss.metadata?.style_intent || "");
  const pipelineOnly = Boolean(miss.metadata?.pipeline_tags_only);
  const sessionSize = Number(miss.metadata?.session_size ?? 0);

  let title = "这轮没有命中";
  let detail = "换个说法，或先用上方配方 chips 试高频选片。";
  const next: { label: string; prompt: string }[] = [
    { label: "交片 10 张", prompt: "选出10张交片" },
    { label: "最炸", prompt: "选出最炸的10张" },
  ];

  if (pipelineOnly || tagStatus === "not_available") {
    title = "语义标签不可用";
    detail =
      sessionSize > 0
        ? `当前场有 ${sessionSize} 张，但多为 Stage2/3 跳过标签。文本搜「吉他手」会空；可先用分数配方，或在 Studio 重跑 Stage3。`
        : "还没有可用的分析结果。";
    next.length = 0;
    next.push(
      { label: "交片短名单", prompt: "选出10张交片" },
      { label: "按能量排序", prompt: "选出最炸的10张" },
    );
  } else if (styleIntent === "slow_shutter") {
    title = "没有真慢门帧";
    detail = "慢门走 RAW ExposureTime，不是 CLIP。可改试交片或气氛短名单。";
  } else if (tagStatus === "available") {
    title = "标签里没命中这句";
    detail = "试试更短的主体词，或走分数配方。";
    next.unshift({ label: "吉他手", prompt: "找出吉他手" });
  }

  return (
    <div className="mt-2 rounded-[5px] border border-white/[0.08] bg-white/[0.02] px-2.5 py-2">
      <p className="text-[11px] font-medium text-white/55">{title}</p>
      <p className="mt-0.5 text-[11px] leading-relaxed text-white/35">{detail}</p>
      {next.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {next.map((n) => (
            <button
              key={n.prompt}
              type="button"
              disabled={disabled}
              onClick={() => onPick(n.prompt)}
              className="rounded-[4px] border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[11px] text-white/50 transition-colors hover:bg-white/[0.07] hover:text-white/75 disabled:opacity-35"
            >
              {n.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

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
  if (phase === "find") return "Step 3 · 找出吉他手";
  if (phase === "style") return "Step 2 · 试试修成一种风格";
  return "Step 1 · 选出得分最高的 10 张";
}

function phaseChipEyebrow(phase: PromptPhase): string {
  if (phase === "find") return "下一步";
  if (phase === "style") return "下一步";
  return "试试这样问";
}

function phaseFollowUpEyebrow(phase: PromptPhase): string {
  if (phase === "find") return "风格好了？再找吉他手";
  if (phase === "style") return "选好了？试试修成一种风格";
  return "试试这样问";
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

const MODE_HINT =
  "当前场次策展：点配方可直接短名单（交片 / 朋友圈 / 最炸…）；也可搜主体、定胶片、导出。打开一张图后会出现「解释这张 / 推荐胶片」。";

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
  const canPreviewVibe = isVibePreviewCall(call);

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
      const items = previewItemsFromCall(call);
      if (items.length > 0) {
        onOpenShowcase(items, "vibe", label, gradeClassFromVibe(vibeMeta));
        return;
      }
    }
    window.dispatchEvent(
      new CustomEvent("luma:gallery-agent-action", {
        detail: {
          action: "reload_vibe",
          tool: call.tool,
          metadata: vibePreviewMetadata(call),
        },
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
  const [focusFile, setFocusFile] = useState("");
  const mode: AgentMode = "gallery";
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

  useEffect(() => {
    const syncFocus = () => {
      const ctx = readGalleryFocusContext();
      setFocusFile(String(ctx.focus_file || "").trim());
    };
    syncFocus();
    window.addEventListener("luma:gallery-focus-changed", syncFocus);
    window.addEventListener("focus", syncFocus);
    return () => {
      window.removeEventListener("luma:gallery-focus-changed", syncFocus);
      window.removeEventListener("focus", syncFocus);
    };
  }, []);

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

  useEffect(() => {
    const prompt = initialPrompt?.trim();
    if (!prompt || consumedInitialPrompt.current) return;
    consumedInitialPrompt.current = true;
    pendingAutoSend.current = prompt;
    setInput(prompt);
    setOpen(true);
  }, [initialPrompt]);

  // Restore the persisted transcript when opening or switching mode/context.
  // Merge carefully: a naive replace drops toolCalls and removes 「打开风格预览」 CTAs.
  useEffect(() => {
    if (!open) return;
    const sid = persistentSessionId(context, mode);
    sessionIdRef.current = sid;
    let cancelled = false;
    void fetchAgentHistory(apiBase, sid, mode).then((hist) => {
      if (cancelled) return;
      setTurns((prev) => mergeHistoryTurns(prev, hist));
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
  }, [open, mode, apiBase, context]);

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
        {
          role: "assistant",
          text: "",
          toolCalls: [],
          streaming: true,
          streamStatus: "正在连接 Agent…",
        },
      ]);
      setSending(true);

      const focusCtx = readGalleryFocusContext();
      const body = {
        session_id: sessionIdRef.current,
        message,
        mode,
        previews_dir: previewsDir ?? undefined,
        focus_file: focusCtx.focus_file,
        selected_files: focusCtx.selected_files,
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
          emitGalleryUiActions(
            fresh,
            turnToolCalls.length ? turnToolCalls : fresh,
            focusCtx.focus_file,
          );
        };
        const { receivedToken } = await streamAgentChat(apiBase, body, {
          onStatus: (status) =>
            patchLastAssistant((t) => ({ ...t, streamStatus: status.message || t.streamStatus })),
          onToken: (text) =>
            patchLastAssistant((t) => ({ ...t, text: t.text + text, streamStatus: undefined })),
          onToolCall: (call) => {
            turnToolCalls.push(call);
            patchLastAssistant((t) => ({
              ...t,
              toolCalls: [...(t.toolCalls ?? []), call],
              streamStatus: "工具执行完成，正在整理结果…",
            }));
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
              streamStatus: undefined,
            }));
            const doneCalls = calls ?? turnToolCalls;
            emitOnce(doneCalls);
            advancePromptPhase(doneCalls);
          },
          onError: (msg) =>
            patchLastAssistant((t) => ({
              ...t,
              text: msg,
              error: true,
              streaming: false,
              streamStatus: undefined,
            })),
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
            streamStatus: undefined,
          }));
          emitGalleryUiActions(
            data.tool_calls ?? [],
            data.tool_calls ?? [],
            focusCtx.focus_file,
          );
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
            streamStatus: undefined,
          }));
          emitGalleryUiActions(
            data.tool_calls ?? [],
            data.tool_calls ?? [],
            focusCtx.focus_file,
          );
          if (!data.error) advancePromptPhase(data.tool_calls);
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : streamErr instanceof Error ? streamErr.message : "请求失败";
          patchLastAssistant((t) => ({
            ...t,
            text: msg,
            error: true,
            streaming: false,
            streamStatus: undefined,
          }));
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
    // Chat transcript ≠ session vibe on disk; clearing chat should drop the sticky
    // grade so the next search preview is not still B&W / Cinestill from last turn.
    void clearSessionVibeApi(apiBase)
      .then(() => {
        window.dispatchEvent(
          new CustomEvent("luma:gallery-agent-action", {
            detail: {
              action: "reload_vibe",
              tool: "apply_film_vibe",
              metadata: { ui_action: "reload_vibe", session_vibe: null },
            },
          }),
        );
      })
      .catch(() => {
        /* ignore — search preview no longer reads session vibe anyway */
      });
  }, [apiBase, context, mode, promptStages]);

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
        <div className="relative flex h-[min(560px,calc(100vh-5.5rem-var(--luma-chat-bottom,1rem)))] w-[min(380px,calc(100vw-2rem))]">
          {/* Left-edge collapse tab — retracts the dock toward the right edge. */}
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="收起策展助手"
            title="收起"
            className="group absolute -left-3.5 top-1/2 z-10 flex h-14 w-3.5 -translate-y-1/2 items-center justify-center rounded-l-[8px] border border-r-0 border-white/[0.1] bg-[#121212]/95 text-white/40 shadow-[-2px_0_12px_rgba(0,0,0,0.35)] backdrop-blur-md transition-colors hover:bg-white/[0.07] hover:text-white/75"
          >
            <svg
              className="h-3 w-3 shrink-0 transition-transform duration-200 group-hover:translate-x-px"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M9 6l6 6-6 6" />
            </svg>
          </button>

          <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[8px] border border-white/[0.1] bg-[#0d0d0d]/95 shadow-2xl backdrop-blur-md">
            <div className="flex shrink-0 items-center justify-between border-b border-white/[0.06] px-3 py-2.5">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-emerald-400/90 shadow-[0_0_10px_rgba(52,211,153,0.5)]" aria-hidden />
                <span className="text-[12px] text-white/70">策展助手</span>
              </div>
              <button
                type="button"
                onClick={resetChat}
                title="清空对话"
                className="rounded-[3px] px-1.5 py-0.5 text-[12px] text-white/35 hover:text-white/60"
              >
                清空
              </button>
            </div>

            <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
              {turns.length === 0 ? (
                <div className="space-y-3 pt-2">
                  <p className="text-[12px] leading-relaxed text-white/35">{MODE_HINT}</p>
                  {promptStages ? (
                    <>
                      <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/28">
                        {phaseStepLabel(promptPhase)}
                      </p>
                      <PromptChipList
                        prompts={promptsForPhase(promptStages, promptPhase)}
                        onPick={(p) => void send(p)}
                        eyebrow={phaseChipEyebrow(promptPhase)}
                      />
                    </>
                  ) : (
                    <>
                      {emptyRotatingPrompts && emptyRotatingPrompts.length > 0 ? (
                        <RotatingPromptStage
                          prompts={emptyRotatingPrompts}
                          onPick={(p) => void send(p)}
                        />
                      ) : null}
                      <div className="space-y-1.5">
                        <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/30">
                          配方 · 点一下就短名单
                        </p>
                        <RecipeChipRow onPick={(p) => void send(p)} disabled={sending} />
                      </div>
                      <div className="flex flex-col gap-1.5">
                        <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/30">
                          也可以这样问
                        </p>
                        {GALLERY_SEMANTIC_SUGGESTIONS.map((s) => (
                          <button
                            key={s}
                            type="button"
                            disabled={sending}
                            onClick={() => void send(s)}
                            className="rounded-[4px] border border-white/[0.06] bg-white/[0.02] px-2.5 py-1.5 text-left text-[12px] text-white/55 transition-colors hover:bg-white/[0.05] hover:text-white/75 disabled:opacity-35"
                          >
                            {s}
                          </button>
                        ))}
                      </div>
                      {focusFile ? (
                        <FocusActionBar
                          focusFile={focusFile}
                          onPick={(p) => void send(p)}
                          disabled={sending}
                        />
                      ) : null}
                    </>
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
                      {t.streaming && !t.text ? (
                        <div className="flex items-center gap-1 text-white/40">
                          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-white/40" />
                          {t.streamStatus || "思考中…"}
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
                        <AssistantActionBar
                          calls={t.toolCalls}
                          onOpenShowcase={openShowcasePreview}
                          apiBase={apiBase}
                        />
                      ) : null}
                      {t.role === "assistant" &&
                      !t.streaming &&
                      t.toolCalls?.length ? (
                        <SearchMissHints
                          calls={t.toolCalls}
                          onPick={(p) => void send(p)}
                          disabled={sending}
                        />
                      ) : null}
                      {t.role === "assistant" &&
                      !t.streaming &&
                      !(t.toolCalls ?? []).some(isVibePreviewCall) &&
                      mentionsVibePreviewCta(t.text) ? (
                        <VibePreviewFallbackButton
                          apiBase={apiBase}
                          fallbackPrompt={
                            i > 0 && turns[i - 1]?.role === "user" ? turns[i - 1].text : undefined
                          }
                        />
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
                {!promptStages && focusFile && !sending ? (
                  <FocusActionBar
                    focusFile={focusFile}
                    onPick={(p) => void send(p)}
                    disabled={sending}
                  />
                ) : null}
                </>
              )}
            </div>

            <div className="shrink-0 border-t border-white/[0.06] p-2.5">
              {!promptStages ? (
                <div className="mb-2">
                  <RecipeChipRow onPick={(p) => void send(p)} disabled={sending} />
                </div>
              ) : null}
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
                  placeholder="例如：选出10张交片 / 有孤独感的吉他手"
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
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="打开策展助手"
          aria-expanded={false}
          title="策展助手"
          className="flex h-12 w-12 items-center justify-center rounded-[14px] border border-white/[0.1] bg-white/[0.08] text-white/80 shadow-lg backdrop-blur-md transition-colors hover:bg-white/[0.14]"
        >
          <img
            src="/brand/luma-icon.png"
            alt=""
            width={32}
            height={32}
            className="h-8 w-8 rounded-[10px] object-cover"
            draggable={false}
          />
        </button>
      )}
    </div>
    </>
  );
}
