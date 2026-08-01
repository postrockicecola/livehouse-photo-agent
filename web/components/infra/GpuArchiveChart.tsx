"use client";

import { useMemo } from "react";

export type GpuArchivePoint = {
  ts: number;
  gpu_util?: number | null;
  gpu_util_source?: string | null;
  gpu_power_w?: number | null;
  img_per_sec?: number | null;
  running?: number | null;
  active_jobs?: Array<{
    job_id: number;
    session_label?: string;
    session_key?: string;
    status?: string;
  }>;
};

type Props = {
  points: GpuArchivePoint[];
  height?: number;
};

function fmtTime(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

/** Full-width archived GPU residency chart (0–100%). */
export function GpuArchiveChart({ points, height = 220 }: Props) {
  const series = useMemo(() => {
    return points
      .map((p) => {
        const u = p.gpu_util;
        if (u == null || !Number.isFinite(Number(u))) return null;
        return { ts: p.ts, pct: Math.round(Math.max(0, Math.min(1, Number(u))) * 100) };
      })
      .filter((x): x is { ts: number; pct: number } => x != null);
  }, [points]);

  const width = 960;
  const padL = 40;
  const padR = 16;
  const padT = 16;
  const padB = 28;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  if (series.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed border-zinc-700/80 bg-zinc-950/40 px-4 font-mono text-xs text-zinc-500"
        style={{ height }}
      >
        Not enough archived GPU samples yet. Keep Infra open (or poll `/api/infra/metrics`) while a
        session runs — samples persist for 7 days.
      </div>
    );
  }

  const t0 = series[0].ts;
  const t1 = series[series.length - 1].ts;
  const tSpan = Math.max(1, t1 - t0);
  const x = (ts: number) => padL + ((ts - t0) / tSpan) * innerW;
  const y = (pct: number) => padT + (1 - pct / 100) * innerH;

  const line = series
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.ts).toFixed(1)},${y(p.pct).toFixed(1)}`)
    .join(" ");
  const area = `${line} L${x(series[series.length - 1].ts).toFixed(1)},${(padT + innerH).toFixed(1)} L${x(
    series[0].ts,
  ).toFixed(1)},${(padT + innerH).toFixed(1)} Z`;

  const yTicks = [0, 25, 50, 75, 100];
  const xLabels = [series[0], series[Math.floor(series.length / 2)], series[series.length - 1]];

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full text-emerald-400/90"
      role="img"
      aria-label="Archived GPU utilization"
    >
      {yTicks.map((tick) => (
        <g key={tick}>
          <line
            x1={padL}
            x2={padL + innerW}
            y1={y(tick)}
            y2={y(tick)}
            stroke="currentColor"
            strokeOpacity={0.08}
          />
          <text
            x={padL - 8}
            y={y(tick) + 3}
            textAnchor="end"
            className="fill-zinc-500"
            style={{ fontSize: 10, fontFamily: "ui-monospace, monospace" }}
          >
            {tick}
          </text>
        </g>
      ))}
      <path d={area} fill="currentColor" fillOpacity={0.12} />
      <path
        d={line}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {xLabels.map((p, i) => (
        <text
          key={`${p.ts}-${i}`}
          x={x(p.ts)}
          y={height - 8}
          textAnchor={i === 0 ? "start" : i === xLabels.length - 1 ? "end" : "middle"}
          className="fill-zinc-500"
          style={{ fontSize: 10, fontFamily: "ui-monospace, monospace" }}
        >
          {fmtTime(p.ts)}
        </text>
      ))}
    </svg>
  );
}
