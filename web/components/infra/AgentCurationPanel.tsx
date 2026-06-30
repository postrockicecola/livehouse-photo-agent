"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { agentThumbUrl, formatLatency, scoreTone, shortName, type AgentRunSummary } from "@/lib/agentRun";

type Props = {
  apiBase: string;
  limit?: number;
};

function statusTone(status?: string | null): string {
  const s = status ?? "";
  if (s === "SUCCEEDED") return "text-emerald-300";
  if (s.startsWith("FAILED") || s === "DEAD_LETTERED") return "text-red-300";
  if (s === "QUEUED") return "text-zinc-400";
  return "text-sky-300";
}

function RunRow({ run, apiBase }: { run: AgentRunSummary; apiBase: string }) {
  const keepers = run.keepers ?? [];
  const analyzed = run.analyzed ?? 0;
  const escalated = run.escalated ?? 0;
  const running = (run.status ?? "") !== "SUCCEEDED" && !String(run.status ?? "").startsWith("FAILED");

  return (
    <div className="rounded-xl border border-stroke/70 bg-panel2/40 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Link href={`/infra/jobs/${run.job_id}`} className="font-mono text-sm text-sky-400 hover:underline">
            #{run.job_id}
          </Link>
          <span className="rounded border border-violet-500/40 bg-violet-950/30 px-1.5 py-0.5 text-[10px] font-medium text-violet-200">
            {run.job_type}
          </span>
          <span className={`font-mono text-xs ${statusTone(run.status)}`}>
            {running ? "● " : ""}
            {run.status}
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px] text-zinc-400">
          <span className="text-sky-300/90">{analyzed} analyzed</span>
          <span className="text-violet-300/90">{escalated} esc</span>
          <span className="text-emerald-300/90">{run.selected_count ?? keepers.length} keep</span>
          <span className="text-zinc-500">{formatLatency(run.total_latency_ms)}</span>
        </div>
      </div>

      {keepers.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {keepers.slice(0, 8).map((k, i) => {
            const url = agentThumbUrl(apiBase, k.image_path, 256);
            return (
              <div key={`${k.image_id ?? i}`} className="w-20">
                <div className="relative aspect-[3/2] overflow-hidden rounded-md border border-stroke bg-zinc-900">
                  {url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={url} alt={k.image_id ?? ""} className="h-full w-full object-cover" loading="lazy" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-[9px] text-zinc-600">—</div>
                  )}
                  {k.escalated ? (
                    <span className="absolute left-0.5 top-0.5 rounded bg-violet-600/80 px-1 text-[8px] text-white">↑</span>
                  ) : null}
                </div>
                <div className={`mt-0.5 text-center font-mono text-[10px] tabular-nums ${scoreTone(k.score)}`}>
                  {k.score != null ? Number(k.score).toFixed(1) : "—"}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="truncate text-[11px] text-zinc-500" title={run.source_dir ?? ""}>
          {running ? "running…" : "0 keepers"} · {shortName(run.source_dir)}
        </div>
      )}
    </div>
  );
}

type SubmitState = { busy: boolean; msg: string | null; ok: boolean };

function CurateForm({ apiBase, onSubmitted }: { apiBase: string; onSubmitted: () => void }) {
  const [sourceDir, setSourceDir] = useState("");
  const [keepers, setKeepers] = useState(10);
  const [maxInferences, setMaxInferences] = useState(20);
  const [planner, setPlanner] = useState<"heuristic" | "llm">("heuristic");
  const [plannerModel, setPlannerModel] = useState("");
  const [state, setState] = useState<SubmitState>({ busy: false, msg: null, ok: false });

  const submit = async () => {
    if (!sourceDir.trim()) {
      setState({ busy: false, msg: "source_dir is required", ok: false });
      return;
    }
    setState({ busy: true, msg: null, ok: false });
    try {
      const params = new URLSearchParams({
        source_dir: sourceDir.trim(),
        target_keepers: String(keepers),
        max_inferences: String(maxInferences),
        planner,
      });
      if (planner === "llm" && plannerModel.trim()) params.set("planner_model", plannerModel.trim());
      const r = await fetch(`${apiBase}/api/tasks/curate?${params.toString()}`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j?.detail ?? `curate ${r.status}`);
      setState({ busy: false, msg: `queued job #${j.job_id} (${j.planner ?? planner})`, ok: true });
      onSubmitted();
    } catch (e) {
      setState({ busy: false, msg: e instanceof Error ? e.message : "submit failed", ok: false });
    }
  };

  const inputCls =
    "rounded-md border border-stroke bg-panel2/60 px-2 py-1 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-violet-500/60 focus:outline-none";

  return (
    <div className="mb-3 rounded-lg border border-stroke/70 bg-panel2/40 p-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-1 min-w-[200px] flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">source dir</span>
          <input
            className={inputCls}
            placeholder="/path/to/Previews"
            value={sourceDir}
            onChange={(e) => setSourceDir(e.target.value)}
          />
        </label>
        <label className="flex w-20 flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">keepers</span>
          <input
            type="number"
            min={1}
            className={inputCls}
            value={keepers}
            onChange={(e) => setKeepers(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <label className="flex w-24 flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">max infer</span>
          <input
            type="number"
            min={1}
            className={inputCls}
            value={maxInferences}
            onChange={(e) => setMaxInferences(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <label className="flex w-28 flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">planner</span>
          <select
            className={inputCls}
            value={planner}
            onChange={(e) => setPlanner(e.target.value as "heuristic" | "llm")}
          >
            <option value="heuristic">heuristic</option>
            <option value="llm">llm</option>
          </select>
        </label>
        {planner === "llm" ? (
          <label className="flex w-40 flex-col gap-1">
            <span className="text-[10px] uppercase tracking-wide text-zinc-500">planner model</span>
            <input
              className={inputCls}
              placeholder="(config default)"
              value={plannerModel}
              onChange={(e) => setPlannerModel(e.target.value)}
            />
          </label>
        ) : null}
        <button
          type="button"
          onClick={submit}
          disabled={state.busy}
          className="rounded-md border border-violet-500/50 bg-violet-600/30 px-3 py-1.5 text-xs font-medium text-violet-100 hover:bg-violet-600/50 disabled:opacity-50"
        >
          {state.busy ? "submitting…" : "Run agent"}
        </button>
      </div>
      {state.msg ? (
        <div className={`mt-2 text-[11px] ${state.ok ? "text-emerald-300" : "text-red-300"}`}>{state.msg}</div>
      ) : null}
    </div>
  );
}

export function AgentCurationPanel({ apiBase, limit = 6 }: Props) {
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(`${apiBase}/api/infra/agent/runs?limit=${limit}`, { cache: "no-store" });
        if (!r.ok) throw new Error(`agent runs ${r.status}`);
        const j = (await r.json()) as { runs?: AgentRunSummary[] };
        if (!cancelled) {
          setRuns(j.runs ?? []);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "failed to load agent runs");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [apiBase, limit, reloadKey]);

  return (
    <section className="glass rounded-xl border border-violet-500/25 bg-violet-950/5 p-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-zinc-100">
            <span aria-hidden>🤖</span> Agentic Curation
          </h2>
          <p className="mt-1 text-xs text-zinc-500">
            ReAct loop on the inference gateway · observe → plan → analyze → reflect/escalate → finalize
          </p>
        </div>
        {error ? <span className="text-xs text-red-300">{error}</span> : null}
      </div>

      <CurateForm apiBase={apiBase} onSubmitted={() => setReloadKey((k) => k + 1)} />

      {loading && !runs.length ? (
        <div className="py-6 text-sm text-zinc-400">Loading agent runs…</div>
      ) : runs.length ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {runs.map((run) => (
            <RunRow key={run.job_id} run={run} apiBase={apiBase} />
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-stroke/70 bg-panel2/50 px-3 py-4 text-sm text-zinc-400">
          No agent runs yet. Kick one off:{" "}
          <code className="rounded bg-zinc-800/80 px-1.5 py-0.5 text-[11px] text-zinc-300">
            POST /api/tasks/curate?source_dir=…&amp;target_keepers=10&amp;max_inferences=20
          </code>
        </div>
      )}
    </section>
  );
}
