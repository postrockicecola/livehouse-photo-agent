"use client";

import { useEffect, useState } from "react";
import { buildPipelineTopology, formatLatencyMs, type InfraStageFlowItem } from "@/lib/infraControlPlane";
import type { InfraSemanticTone } from "@/lib/infraVisualTokens";
import { ControlPlaneSection, LivePulse } from "./ControlPlaneSection";

type Props = {
  stages: InfraStageFlowItem[];
  jobsByStatus: Record<string, number>;
  loading?: boolean;
};

function nodeTone(failed: number, active: number): InfraSemanticTone {
  if (failed > 0 && active > 0) return "warning";
  if (failed > 0) return "failure";
  if (active > 0) return "success";
  return "neutral";
}

// Color discipline: healthy/active is calm; glow + ring reserved for anomalies.
const NODE_RING: Record<InfraSemanticTone, string> = {
  success: "border-emerald-500/30",
  warning: "border-amber-500/50 ring-1 ring-amber-500/15",
  failure: "border-red-500/55 ring-1 ring-red-500/20 shadow-[0_0_22px_-10px_rgba(248,113,113,0.5)]",
  neutral: "border-stroke/90",
};

function FlowConnector({ flow, animate }: { flow: number; animate: boolean }) {
  const hot = flow > 0;
  return (
    <div className="hidden flex-none flex-col items-center justify-center px-1 sm:flex lg:px-2" aria-hidden>
      <div
        className={`h-px w-8 lg:w-12 ${hot ? "infra-topology-flow runtime-flow-line bg-emerald-500/25" : "bg-zinc-800"}`}
      />
      <span
        className={`mt-1 font-mono text-[9px] tabular-nums ${
          hot && animate ? "text-emerald-300/90 runtime-flow-pulse" : hot ? "text-emerald-400/80" : "text-zinc-700"
        }`}
      >
        {flow > 0 ? flow : "·"}
      </span>
      <div
        className={`mt-1 h-px w-8 lg:w-12 ${hot ? "infra-topology-flow runtime-flow-line bg-emerald-500/25" : "bg-zinc-800"}`}
      />
    </div>
  );
}

export function PipelineTopology({ stages, jobsByStatus, loading }: Props) {
  const nodes = buildPipelineTopology(stages, jobsByStatus);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 3000);
    return () => clearInterval(t);
  }, []);

  const animate = tick % 2 === 0;

  return (
    <ControlPlaneSection eyebrow="Pipeline" title="Topology" right={<LivePulse />}>
      <div className="overflow-x-auto pb-1">
        <div key={tick} className="infra-panel-refresh flex min-w-[720px] items-stretch gap-0">
          {nodes.map((node, idx) => {
            const tone = nodeTone(node.failed, node.active);
            return (
              <div key={node.id} className="flex flex-1 items-center">
                <div
                  className={`flex min-h-[6.75rem] flex-1 flex-col rounded-xl border bg-panel2/60 p-4 transition-colors duration-500 ${NODE_RING[tone]}`}
                >
                  <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">{node.label}</div>
                  <div className="mt-3 grid flex-1 grid-cols-3 gap-2 text-center">
                    <div>
                      <div className="text-xl font-semibold tabular-nums text-emerald-100/95 sm:text-2xl">
                        {loading ? "…" : node.processed}
                      </div>
                      <div className="text-[9px] uppercase tracking-wide text-zinc-600">done</div>
                    </div>
                    <div>
                      <div
                        className={`text-xl font-semibold tabular-nums sm:text-2xl ${
                          node.failed > 0 ? "text-red-300" : "text-zinc-500"
                        }`}
                      >
                        {loading ? "…" : node.failed}
                      </div>
                      <div className="text-[9px] uppercase tracking-wide text-zinc-600">fail</div>
                    </div>
                    <div>
                      <div
                        className={`text-xl font-semibold tabular-nums sm:text-2xl ${
                          node.active > 0 ? "text-emerald-200" : "text-zinc-600"
                        }`}
                      >
                        {loading ? "…" : node.active}
                      </div>
                      <div className="text-[9px] uppercase tracking-wide text-zinc-600">live</div>
                    </div>
                  </div>
                  <div className="mt-2 text-center font-mono text-[10px] text-zinc-600">
                    {loading ? "…" : formatLatencyMs(node.avgLatencyMs)}
                  </div>
                </div>
                {idx < nodes.length - 1 ? <FlowConnector flow={node.flowHint} animate={animate} /> : null}
              </div>
            );
          })}
        </div>
      </div>
    </ControlPlaneSection>
  );
}
