"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppNav } from "@/components/ui/AppNav";
import { ShowcaseBanner } from "@/components/ShowcaseBanner";
import { GpuArchiveChart, type GpuArchivePoint } from "@/components/infra/GpuArchiveChart";
import { getApiBase } from "@/lib/apiBase";

const API_BASE = getApiBase();

const WINDOWS: Array<{ label: string; sec: number; limit: number }> = [
  { label: "1h", sec: 3600, limit: 240 },
  { label: "6h", sec: 6 * 3600, limit: 480 },
  { label: "24h", sec: 86400, limit: 720 },
  { label: "7d", sec: 7 * 86400, limit: 2000 },
];

type GpuHistoryResponse = {
  count?: number;
  window_sec?: number;
  points?: GpuArchivePoint[];
};

function pct(u: number | null | undefined): number | null {
  if (u == null || !Number.isFinite(Number(u))) return null;
  return Math.round(Math.max(0, Math.min(1, Number(u))) * 100);
}

export default function InfraGpuArchivePage() {
  const [windowIdx, setWindowIdx] = useState(2);
  const [points, setPoints] = useState<GpuArchivePoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);

  const win = WINDOWS[windowIdx] ?? WINDOWS[2];

  const load = useCallback(async () => {
    try {
      const r = await fetch(
        `${API_BASE}/api/infra/metrics/gpu-history?window_sec=${win.sec}&limit=${win.limit}`,
        { cache: "no-store" },
      );
      if (!r.ok) throw new Error(`gpu-history ${r.status}`);
      const j = (await r.json()) as GpuHistoryResponse;
      setPoints(j.points ?? []);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    } finally {
      setLoading(false);
    }
  }, [win.limit, win.sec]);

  useEffect(() => {
    setLoading(true);
    void load();
    const t = setInterval(() => void load(), 15000);
    return () => clearInterval(t);
  }, [load]);

  const stats = useMemo(() => {
    const vals = points.map((p) => pct(p.gpu_util)).filter((v): v is number => v != null);
    if (!vals.length) {
      return { n: 0, avg: null as number | null, peak: null as number | null, realN: 0 };
    }
    const avg = Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
    const peak = Math.max(...vals);
    const realN = points.filter((p) => p.gpu_util_source === "powermetrics" && p.gpu_util != null).length;
    return { n: vals.length, avg, peak, realN };
  }, [points]);

  const sessionHits = useMemo(() => {
    const map = new Map<string, { label: string; jobIds: Set<number>; hits: number }>();
    for (const p of points) {
      for (const j of p.active_jobs ?? []) {
        const label = j.session_label || j.session_key || `job-${j.job_id}`;
        const key = label;
        const cur = map.get(key) ?? { label, jobIds: new Set<number>(), hits: 0 };
        cur.hits += 1;
        cur.jobIds.add(j.job_id);
        map.set(key, cur);
      }
    }
    return [...map.values()]
      .sort((a, b) => b.hits - a.hits)
      .slice(0, 12)
      .map((x) => ({
        label: x.label,
        hits: x.hits,
        jobIds: [...x.jobIds].sort((a, b) => b - a).slice(0, 4),
      }));
  }, [points]);

  return (
    <main className="min-h-screen px-3 py-4 sm:px-6 sm:py-6">
      <ShowcaseBanner />
      <AppNav />

      <header className="mt-6 mb-5 max-w-5xl">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-zinc-500">
          Infra · archived telemetry
        </p>
        <h1 className="mt-1 font-display text-2xl tracking-tight text-zinc-100 sm:text-3xl">
          GPU archive
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-400">
          Persisted GPU / VLM serving curve from control-plane samples (7-day retain). Open{" "}
          <Link href="/infra" className="text-sky-400 hover:underline">
            Infra
          </Link>{" "}
          while a session runs so points keep writing; for real Apple GPU residency also run{" "}
          <code className="rounded bg-zinc-900 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
            sudo python scripts/gpu_telemetry_sampler.py
          </code>
          .
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {WINDOWS.map((w, i) => (
          <button
            key={w.label}
            type="button"
            onClick={() => setWindowIdx(i)}
            className={`rounded-md px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50 ${
              windowIdx === i
                ? "bg-zinc-800 text-zinc-100"
                : "border border-zinc-800 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            {w.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => void load()}
          className="ml-auto rounded-md border border-zinc-800 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-400 hover:text-zinc-200"
        >
          Refresh
        </button>
      </div>

      {error ? (
        <p className="mb-4 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 font-mono text-xs text-red-300">
          {error}
        </p>
      ) : null}

      <section className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { k: "Samples", v: loading ? "…" : String(stats.n) },
          { k: "Avg GPU", v: stats.avg != null ? `${stats.avg}%` : "—" },
          { k: "Peak GPU", v: stats.peak != null ? `${stats.peak}%` : "—" },
          { k: "Real (powermetrics)", v: loading ? "…" : String(stats.realN) },
        ].map((c) => (
          <div
            key={c.k}
            className="rounded-lg border border-stroke/70 bg-zinc-950/50 px-3 py-3"
          >
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-500">{c.k}</p>
            <p className="mt-1 font-mono text-xl text-zinc-100">{c.v}</p>
          </div>
        ))}
      </section>

      <section className="mb-6 rounded-lg border border-stroke/70 bg-zinc-950/40 p-3 sm:p-4">
        <div className="mb-2 flex items-baseline justify-between gap-3">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-500">
            GPU utilization
          </h2>
          <p className="font-mono text-[10px] text-zinc-600">
            {updatedAt ? `updated ${new Date(updatedAt).toLocaleTimeString()}` : "—"}
          </p>
        </div>
        <GpuArchiveChart points={points} />
      </section>

      <section className="max-w-3xl">
        <h2 className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-500">
          Sessions seen in window
        </h2>
        {!sessionHits.length ? (
          <p className="font-mono text-xs text-zinc-600">
            No active-job annotations in this window yet (older samples predate this field, or no
            pipeline jobs were running while sampling).
          </p>
        ) : (
          <ul className="divide-y divide-zinc-800/80 rounded-lg border border-stroke/70 bg-zinc-950/40">
            {sessionHits.map((s) => (
              <li
                key={s.label}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5"
              >
                <div>
                  <p className="font-mono text-sm text-zinc-200">{s.label}</p>
                  <p className="font-mono text-[10px] text-zinc-600">
                    {s.hits} samples · jobs{" "}
                    {s.jobIds.map((id, i) => (
                      <span key={id}>
                        {i > 0 ? ", " : ""}
                        <Link href={`/infra/jobs/${id}`} className="text-sky-400 hover:underline">
                          #{id}
                        </Link>
                      </span>
                    ))}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
