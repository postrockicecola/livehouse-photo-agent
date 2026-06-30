"use client";

import { useId } from "react";

type Props = {
  data: Array<number | null>;
  width?: number;
  height?: number;
  /** stroke color class via currentColor; pass through className for color */
  className?: string;
  /** Draw a soft area fill under the line. */
  fill?: boolean;
};

/**
 * Minimal inline SVG sparkline. Renders REAL client-observed samples only
 * (accumulated since page open); null gaps are skipped, not interpolated.
 */
export function Sparkline({ data, width = 96, height = 28, className = "text-emerald-400/80", fill = true }: Props) {
  const gradId = useId();
  const pts = data.filter((v): v is number => v != null && Number.isFinite(v));

  if (pts.length < 2) {
    return (
      <svg width={width} height={height} className={className} aria-hidden role="img">
        <line
          x1={0}
          y1={height - 1}
          x2={width}
          y2={height - 1}
          stroke="currentColor"
          strokeOpacity={0.25}
          strokeDasharray="2 3"
        />
      </svg>
    );
  }

  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const stepX = width / (pts.length - 1);
  const y = (v: number) => height - 2 - ((v - min) / span) * (height - 4);

  const line = pts.map((v, i) => `${i === 0 ? "M" : "L"}${(i * stepX).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${((pts.length - 1) * stepX).toFixed(1)},${height} L0,${height} Z`;

  return (
    <svg width={width} height={height} className={className} aria-hidden role="img" preserveAspectRatio="none">
      {fill ? (
        <>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="currentColor" stopOpacity={0.22} />
              <stop offset="100%" stopColor="currentColor" stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradId})`} stroke="none" />
        </>
      ) : null}
      <path d={line} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
