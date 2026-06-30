"use client";

import { formatLatencyMs } from "@/lib/infraControlPlane";
import { ControlPlaneSection } from "./ControlPlaneSection";

type ProviderRuntime = {
  requests?: number;
  failures?: number;
  fallbacks?: number;
  avg_latency_ms?: number | null;
  last_latency_ms?: number | null;
};

export type ProviderRow = {
  name: string;
  display_name?: string;
  enabled: boolean;
  endpoint?: string | null;
  model_name?: string | null;
  fallback_model_name?: string | null;
  runtime?: ProviderRuntime | Record<string, unknown> | null;
};

type Props = {
  providers: ProviderRow[];
  activeProvider: string;
  loading?: boolean;
};

function healthFromRuntime(rt: ProviderRuntime | null | undefined, enabled: boolean): {
  label: string;
  tone: "ok" | "warn" | "down" | "off";
} {
  if (!enabled) return { label: "Disabled", tone: "off" };
  if (!rt) return { label: "No samples", tone: "warn" };
  const req = Number(rt.requests ?? 0);
  const fail = Number(rt.failures ?? 0);
  if (req === 0) return { label: "Idle", tone: "warn" };
  const rate = req > 0 ? (req - fail) / req : 0;
  if (rate >= 0.95) return { label: "Healthy", tone: "ok" };
  if (rate >= 0.8) return { label: "Degraded", tone: "warn" };
  return { label: "Unhealthy", tone: "down" };
}

export function ProviderRuntimePanel({ providers, activeProvider, loading }: Props) {
  return (
    <ControlPlaneSection
      eyebrow="Inference"
      title="Provider Runtime"
      subtitle="Counters from the gallery/infra API process; multi-worker deployments should cross-check model_runs in Brain"
      right={
        <span className="font-mono text-[10px] text-zinc-500">
          active <span className="text-emerald-200/90">{activeProvider || "—"}</span>
        </span>
      }
    >
      <div className="grid gap-3 md:grid-cols-2">
        {loading ? (
          <div className="col-span-full py-8 text-sm text-zinc-400">Loading providers…</div>
        ) : providers.length === 0 ? (
          <div className="col-span-full text-sm text-zinc-500">No providers configured</div>
        ) : (
          providers.map((p) => {
            const rt = p.runtime as ProviderRuntime | null | undefined;
            const hasRt = rt && typeof rt === "object" && "requests" in rt;
            const req = Number(hasRt ? rt.requests ?? 0 : 0);
            const fail = Number(hasRt ? rt.failures ?? 0 : 0);
            const fb = Number(hasRt ? rt.fallbacks ?? 0 : 0);
            const successPct = req > 0 ? Math.round(((req - fail) / req) * 100) : null;
            const health = healthFromRuntime(hasRt ? rt : null, p.enabled);
            const border =
              health.tone === "ok"
                ? "border-emerald-500/30"
                : health.tone === "down"
                  ? "border-red-500/35"
                  : health.tone === "warn"
                    ? "border-amber-500/30"
                    : "border-stroke";

            return (
              <div key={p.name} className={`rounded-xl border bg-panel2/70 p-4 ${border}`}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-base font-semibold text-zinc-100">{p.display_name ?? p.name}</div>
                  <span
                    className={
                      health.tone === "ok"
                        ? "text-xs text-emerald-300"
                        : health.tone === "down"
                          ? "text-xs text-red-300"
                          : health.tone === "warn"
                            ? "text-xs text-amber-300"
                            : "text-xs text-zinc-500"
                    }
                  >
                    {health.label}
                  </span>
                </div>
                <div className="mt-1 text-xs text-zinc-500">
                  {p.model_name ?? "—"}
                  {p.fallback_model_name ? ` · fallback → ${p.fallback_model_name}` : ""}
                </div>
                <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div>
                    <div className="text-2xl font-semibold tabular-nums text-zinc-50">{req}</div>
                    <div className="text-[9px] uppercase text-zinc-600">requests</div>
                  </div>
                  <div>
                    <div className="text-2xl font-semibold tabular-nums text-zinc-50">
                      {formatLatencyMs(hasRt ? rt.avg_latency_ms : null)}
                    </div>
                    <div className="text-[9px] uppercase text-zinc-600">avg latency</div>
                  </div>
                  <div>
                    <div className={`text-2xl font-semibold tabular-nums ${successPct != null && successPct < 90 ? "text-amber-200" : "text-emerald-100"}`}>
                      {successPct != null ? `${successPct}%` : "—"}
                    </div>
                    <div className="text-[9px] uppercase text-zinc-600">success</div>
                  </div>
                  <div>
                    <div className={`text-2xl font-semibold tabular-nums ${fb > 0 ? "text-amber-200" : "text-zinc-400"}`}>
                      {fb}
                    </div>
                    <div className="text-[9px] uppercase text-zinc-600">fallbacks</div>
                  </div>
                </div>
                {p.endpoint ? (
                  <div className="mt-2 truncate font-mono text-[10px] text-zinc-600">{p.endpoint}</div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </ControlPlaneSection>
  );
}
