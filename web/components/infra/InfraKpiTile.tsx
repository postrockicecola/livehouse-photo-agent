"use client";

import {
  INFRA_KPI_CARD,
  INFRA_KPI_HINT,
  INFRA_KPI_LABEL,
  INFRA_KPI_VALUE,
  INFRA_TONE_BORDER,
  INFRA_TONE_VALUE,
  type InfraSemanticTone,
} from "@/lib/infraVisualTokens";

type Props = {
  label: string;
  value: string | number;
  hint?: string;
  tone?: InfraSemanticTone;
  loading?: boolean;
  compactValue?: boolean;
};

export function InfraKpiTile({ label, value, hint, tone = "neutral", loading, compactValue }: Props) {
  return (
    <div className={`${INFRA_KPI_CARD} ${INFRA_TONE_BORDER[tone]}`}>
      <div>
        <div className={INFRA_KPI_LABEL}>{label}</div>
        <div className={`${INFRA_KPI_VALUE} ${INFRA_TONE_VALUE[tone]} ${compactValue ? "text-3xl sm:text-4xl" : ""}`}>
          {loading ? "…" : value}
        </div>
      </div>
      {hint ? <p className={INFRA_KPI_HINT}>{hint}</p> : <span className="block min-h-[1.25rem]" aria-hidden />}
    </div>
  );
}
