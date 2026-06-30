"use client";

import Link from "next/link";
import { shortPath } from "./utils";
import type { BrainDashboardData } from "./types";

function fmtTs(sec: unknown): string {
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return new Date(n * 1000).toLocaleString();
}

type Props = {
  data: BrainDashboardData | null;
  loading?: boolean;
};

export function LedgerSection({ data, loading }: Props) {
  const tables = Object.entries(data?.table_counts ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const photoStatuses = Object.entries(data?.photos_by_status ?? {});
  const jobTypes = Object.entries(data?.jobs_by_type ?? {});

  return (
    <section className="rounded-2xl border border-stroke/60 bg-[#060708]">
      <div className="border-b border-stroke/50 px-4 py-3 sm:px-5">
        <h2 className="text-xs uppercase tracking-[0.22em] text-zinc-600">Ledger · drill-down</h2>
        <p className="mt-1 font-mono text-[10px] text-zinc-700">
          {data?.db_path ?? (loading ? "…" : "—")} · sessions / photos / artifacts SSOT
        </p>
      </div>

      <div className="space-y-4 p-4 sm:p-5">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-stroke/40 bg-[#08090c] p-3">
            <h3 className="text-[10px] uppercase tracking-[0.16em] text-zinc-600">Table counts</h3>
            <div className="mt-2 max-h-48 overflow-y-auto">
              <table className="w-full text-left font-mono text-[10px]">
                <tbody className="text-zinc-500">
                  {tables.map(([name, count]) => (
                    <tr key={name} className="border-t border-stroke/30 first:border-0">
                      <td className="py-1 pr-4">{name}</td>
                      <td className="py-1 text-right tabular-nums text-zinc-400">{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-xl border border-stroke/40 bg-[#08090c] p-3">
            <h3 className="text-[10px] uppercase tracking-[0.16em] text-zinc-600">Outcome breakdown</h3>
            <ul className="mt-2 space-y-1 font-mono text-[10px]">
              {photoStatuses.map(([st, n]) => (
                <li key={st} className="flex justify-between text-zinc-500">
                  <span className="text-amber-200/60">{st}</span>
                  <span className="tabular-nums">{n}</span>
                </li>
              ))}
              {jobTypes.map(([t, n]) => (
                <li key={t} className="flex justify-between text-zinc-500">
                  <span className="text-sky-300/60">{t}</span>
                  <span className="tabular-nums">{n}</span>
                </li>
              ))}
              {!photoStatuses.length && !jobTypes.length ? (
                <li className="text-zinc-700">no ledger aggregates</li>
              ) : null}
            </ul>
          </div>
        </div>

        <details className="group rounded-xl border border-stroke/40 bg-[#08090c]">
          <summary className="cursor-pointer list-none px-3 py-2.5 font-mono text-[11px] text-zinc-500 marker:content-none hover:text-zinc-400">
            sessions ({data?.limits?.sessions ?? 25}) · expand
          </summary>
          <div className="overflow-x-auto border-t border-stroke/30 px-3 py-2">
            <table className="w-full min-w-[640px] text-left font-mono text-[10px] text-zinc-500">
              <thead>
                <tr className="text-zinc-600">
                  <th className="pb-1 pr-2 font-normal">id</th>
                  <th className="pb-1 pr-2 font-normal">key</th>
                  <th className="pb-1 pr-2 text-right font-normal">ingested</th>
                  <th className="pb-1 pr-2 text-right font-normal">analyzed</th>
                  <th className="pb-1 font-normal">previews</th>
                </tr>
              </thead>
              <tbody>
                {(data?.sessions ?? []).map((s) => (
                  <tr key={String(s.id)} className="border-t border-stroke/20">
                    <td className="py-1 pr-2">{String(s.id)}</td>
                    <td className="py-1 pr-2">{String(s.session_key ?? "")}</td>
                    <td className="py-1 pr-2 text-right tabular-nums">{String(s.photos_ingested ?? 0)}</td>
                    <td className="py-1 pr-2 text-right tabular-nums">{String(s.photos_analyzed ?? 0)}</td>
                    <td className="py-1" title={String(s.previews_dir ?? "")}>
                      {shortPath(s.previews_dir, 40)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        <details className="group rounded-xl border border-stroke/40 bg-[#08090c]">
          <summary className="cursor-pointer list-none px-3 py-2.5 font-mono text-[11px] text-zinc-500 marker:content-none hover:text-zinc-400">
            photos ({data?.limits?.photos ?? 50}) · expand
          </summary>
          <div className="overflow-x-auto border-t border-stroke/30 px-3 py-2">
            <table className="w-full min-w-[720px] text-left font-mono text-[10px] text-zinc-500">
              <thead>
                <tr className="text-zinc-600">
                  <th className="pb-1 pr-2 font-normal">id</th>
                  <th className="pb-1 pr-2 font-normal">status</th>
                  <th className="pb-1 pr-2 font-normal">session</th>
                  <th className="pb-1 pr-2 font-normal">updated</th>
                  <th className="pb-1 font-normal">path</th>
                </tr>
              </thead>
              <tbody>
                {(data?.photos ?? []).map((p) => (
                  <tr key={String(p.id)} className="border-t border-stroke/20">
                    <td className="py-1 pr-2">{String(p.id)}</td>
                    <td className="py-1 pr-2 text-amber-200/60">{String(p.status ?? "")}</td>
                    <td className="py-1 pr-2">{String(p.session_key ?? p.session_id ?? "")}</td>
                    <td className="py-1 pr-2 whitespace-nowrap">{fmtTs(p.updated_at ?? p.created_at)}</td>
                    <td className="py-1" title={String(p.file_path ?? "")}>
                      {shortPath(p.file_path, 44)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        <p className="font-mono text-[10px] text-zinc-700">
          execution state ·{" "}
          <Link href="/infra" className="text-zinc-500 hover:text-zinc-400">
            Jobs / Workers console
          </Link>
        </p>
      </div>
    </section>
  );
}
