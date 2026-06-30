"use client";

import Link from "next/link";

export type BrainDashboardData = {
  db_path?: string;
  table_counts?: Record<string, number>;
  photos_by_status?: Record<string, number>;
  jobs_by_type?: Record<string, number>;
  sessions?: Array<Record<string, unknown>>;
  photos?: Array<Record<string, unknown>>;
  limits?: { sessions?: number; photos?: number };
};

function fmtTs(sec: unknown): string {
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return new Date(n * 1000).toLocaleString();
}

function shortPath(p: unknown, max = 56): string {
  const s = String(p ?? "");
  if (s.length <= max) return s;
  return `…${s.slice(-max + 1)}`;
}

type Props = {
  data: BrainDashboardData | null;
  loading?: boolean;
};

export function BrainDashboard({ data, loading }: Props) {
  const tables = Object.entries(data?.table_counts ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const photoStatuses = Object.entries(data?.photos_by_status ?? {});
  const jobTypes = Object.entries(data?.jobs_by_type ?? {});

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-stroke bg-surface/40 p-4">
        <h2 className="text-sm font-medium text-zinc-200">Database</h2>
        <p className="mt-1 break-all font-mono text-xs text-zinc-500">{data?.db_path ?? (loading ? "…" : "—")}</p>
        <p className="mt-2 text-xs text-zinc-500">
          <code className="text-zinc-400">photos.status</code> = 入库/结果台账；执行态见{" "}
          <Link className="text-sky-400 hover:underline" href="/infra">
            Infra → jobs
          </Link>
          。
        </p>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-stroke bg-surface/40 p-4">
          <h2 className="text-sm font-medium text-zinc-200">Table row counts</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="pb-2 pr-4 font-normal">table</th>
                  <th className="pb-2 font-normal text-right">rows</th>
                </tr>
              </thead>
              <tbody className="text-zinc-300">
                {tables.map(([name, count]) => (
                  <tr key={name} className="border-t border-stroke/60">
                    <td className="py-1.5 pr-4 font-mono">{name}</td>
                    <td className="py-1.5 text-right tabular-nums">{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rounded-xl border border-stroke bg-surface/40 p-4">
          <h2 className="text-sm font-medium text-zinc-200">Photos by status</h2>
          <ul className="mt-3 space-y-1 text-xs">
            {photoStatuses.length === 0 ? (
              <li className="text-zinc-500">No rows</li>
            ) : (
              photoStatuses.map(([st, n]) => (
                <li key={st} className="flex justify-between gap-4">
                  <span className="font-mono text-amber-200/90">{st}</span>
                  <span className="tabular-nums text-zinc-300">{n}</span>
                </li>
              ))
            )}
          </ul>
          <h3 className="mt-4 text-xs font-medium text-zinc-400">Jobs by type</h3>
          <ul className="mt-2 space-y-1 text-xs">
            {jobTypes.length === 0 ? (
              <li className="text-zinc-500">No rows</li>
            ) : (
              jobTypes.map(([t, n]) => (
                <li key={t} className="flex justify-between gap-4">
                  <span className="font-mono text-sky-300/90">{t}</span>
                  <span className="tabular-nums text-zinc-300">{n}</span>
                </li>
              ))
            )}
          </ul>
        </section>
      </div>

      <section className="rounded-xl border border-stroke bg-surface/40 p-4">
        <h2 className="text-sm font-medium text-zinc-200">
          Sessions <span className="font-normal text-zinc-500">(latest {data?.limits?.sessions ?? 25})</span>
        </h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="text-zinc-500">
              <tr>
                <th className="pb-2 pr-3 font-normal">id</th>
                <th className="pb-2 pr-3 font-normal">key</th>
                <th className="pb-2 pr-3 font-normal">device</th>
                <th className="pb-2 pr-3 font-normal text-right">INGESTED</th>
                <th className="pb-2 pr-3 font-normal text-right">ANALYZED</th>
                <th className="pb-2 pr-3 font-normal">started</th>
                <th className="pb-2 font-normal">previews_dir</th>
              </tr>
            </thead>
            <tbody className="text-zinc-300">
              {(data?.sessions ?? []).map((s) => (
                <tr key={String(s.id)} className="border-t border-stroke/60">
                  <td className="py-1.5 pr-3 font-mono">{String(s.id)}</td>
                  <td className="py-1.5 pr-3">{String(s.session_key ?? "")}</td>
                  <td className="py-1.5 pr-3 font-mono text-zinc-500">{String(s.device_id ?? "")}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-amber-200/80">
                    {String(s.photos_ingested ?? 0)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-emerald-300/80">
                    {String(s.photos_analyzed ?? 0)}
                  </td>
                  <td className="py-1.5 pr-3 whitespace-nowrap text-zinc-500">{fmtTs(s.started_at)}</td>
                  <td className="py-1.5 font-mono text-[10px] text-zinc-500" title={String(s.previews_dir ?? "")}>
                    {shortPath(s.previews_dir, 48)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-xl border border-stroke bg-surface/40 p-4">
        <h2 className="text-sm font-medium text-zinc-200">
          Photos <span className="font-normal text-zinc-500">(latest {data?.limits?.photos ?? 50})</span>
        </h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[800px] text-left text-xs">
            <thead className="text-zinc-500">
              <tr>
                <th className="pb-2 pr-3 font-normal">id</th>
                <th className="pb-2 pr-3 font-normal">status</th>
                <th className="pb-2 pr-3 font-normal">session</th>
                <th className="pb-2 pr-3 font-normal">hash</th>
                <th className="pb-2 pr-3 font-normal">updated</th>
                <th className="pb-2 font-normal">path</th>
              </tr>
            </thead>
            <tbody className="text-zinc-300">
              {(data?.photos ?? []).map((p) => (
                <tr key={String(p.id)} className="border-t border-stroke/60">
                  <td className="py-1.5 pr-3 font-mono">{String(p.id)}</td>
                  <td className="py-1.5 pr-3 font-mono text-amber-200/90">{String(p.status ?? "")}</td>
                  <td className="py-1.5 pr-3">
                    {p.session_id != null ? (
                      <span className="font-mono text-zinc-400">
                        {String(p.session_key ?? p.session_id)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[10px] text-zinc-500">
                    {String(p.file_hash ?? "").slice(0, 12)}…
                  </td>
                  <td className="py-1.5 pr-3 whitespace-nowrap text-zinc-500">
                    {fmtTs(p.updated_at ?? p.created_at)}
                  </td>
                  <td className="py-1.5 font-mono text-[10px] text-zinc-500" title={String(p.file_path ?? "")}>
                    {shortPath(p.file_path, 52)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
