import { provenanceMeta, type ProvenanceKind } from "@/lib/provenance";

const TONE: Record<ProvenanceKind, string> = {
  live: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200/85",
  recorded: "border-sky-400/25 bg-sky-400/10 text-sky-200/80",
  simulated: "border-amber-400/30 bg-amber-400/10 text-amber-200/85",
  showcase: "border-amber-400/25 bg-amber-400/[0.08] text-amber-200/80",
};

type Props = {
  kind: ProvenanceKind;
  className?: string;
  /** Show longer description in title tooltip */
  showTitle?: boolean;
};

export function ProvenanceBadge({ kind, className = "", showTitle = true }: Props) {
  const meta = provenanceMeta(kind);
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.14em] ${TONE[kind]} ${className}`}
      title={showTitle ? meta.description : undefined}
    >
      {meta.label}
    </span>
  );
}
