/**
 * Unified data-provenance labels for portfolio / showcase surfaces.
 * Interviewers must never confuse Live, Recorded, Simulated, and Showcase Fixture.
 */
import { isShowcaseClient } from "@/lib/showcase";

export type ProvenanceKind = "live" | "recorded" | "simulated" | "showcase";

export type ProvenanceMeta = {
  id: ProvenanceKind;
  /** Short chip label shown in UI */
  label: string;
  description: string;
};

export const PROVENANCE: Record<ProvenanceKind, ProvenanceMeta> = {
  live: {
    id: "live",
    label: "Live",
    description: "From the currently running system (local full stack).",
  },
  recorded: {
    id: "recorded",
    label: "Recorded Run",
    description: "From a real, reproducible historical run or archive snapshot.",
  },
  simulated: {
    id: "simulated",
    label: "Simulated",
    description: "Shape demo or injected latency — not a production measurement.",
  },
  showcase: {
    id: "showcase",
    label: "Showcase Fixture",
    description: "Committed static snapshot for the read-only deploy (no live backend/GPU).",
  },
} as const;

/** Client-side: showcase deploy wins; otherwise Live unless caller marks fallback/simulated. */
export function resolveClientProvenance(opts?: {
  fallback?: boolean;
  simulated?: boolean;
}): ProvenanceKind {
  if (opts?.simulated) return "simulated";
  if (isShowcaseClient()) return "showcase";
  if (opts?.fallback) return "recorded";
  return "live";
}

export function provenanceMeta(kind: ProvenanceKind): ProvenanceMeta {
  return PROVENANCE[kind];
}
