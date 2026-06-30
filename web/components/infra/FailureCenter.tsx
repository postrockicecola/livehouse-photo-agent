"use client";

import type { FailureBuckets } from "@/lib/infraControlPlane";
import { DeadLetterPanel, type DeadLetterJobRow } from "@/components/DeadLetterPanel";
import { ControlPlaneSection } from "./ControlPlaneSection";

type Props = {
  buckets: FailureBuckets;
  deadLetterItems: DeadLetterJobRow[];
  loading?: boolean;
  apiBase: string;
};

function BucketCard({
  label,
  value,
  emphasize,
}: {
  label: string;
  value: number;
  emphasize?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border p-4 ${
        emphasize ? "border-red-500/35 bg-red-950/10" : "border-stroke bg-panel2/60"
      }`}
    >
      <div className="text-[10px] uppercase tracking-[0.14em] text-zinc-500">{label}</div>
      <div className={`mt-2 text-3xl font-semibold tabular-nums ${emphasize ? "text-red-200" : "text-zinc-100"}`}>
        {value}
      </div>
    </div>
  );
}

export function FailureCenter({ buckets, deadLetterItems, loading, apiBase }: Props) {
  const anyHot =
    buckets.stage1Failures > 0 ||
    buckets.providerTimeouts > 0 ||
    buckets.fileReadErrors > 0 ||
    buckets.exportFailures > 0 ||
    buckets.deadLetterCount > 0;

  return (
    <ControlPlaneSection
      id="dead-letter"
      eyebrow="Reliability"
      title="Failure Center"
      subtitle="Stage aggregates · model_runs error_type · dead-letter queue for manual retry"
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <BucketCard label="Stage1 failures" value={loading ? 0 : buckets.stage1Failures} emphasize={buckets.stage1Failures > 0} />
        <BucketCard
          label="Provider timeouts"
          value={loading ? 0 : buckets.providerTimeouts}
          emphasize={buckets.providerTimeouts > 0}
        />
        <BucketCard label="File read errors" value={loading ? 0 : buckets.fileReadErrors} emphasize={buckets.fileReadErrors > 0} />
        <BucketCard label="Export failures" value={loading ? 0 : buckets.exportFailures} emphasize={buckets.exportFailures > 0} />
        <BucketCard label="Dead-letter" value={loading ? 0 : buckets.deadLetterCount} emphasize={buckets.deadLetterCount > 0} />
        <BucketCard label="Retry signals" value={loading ? 0 : buckets.retryCount} emphasize={false} />
      </div>
      {!loading && !anyHot ? (
        <p className="mt-3 text-xs text-emerald-300/80">No elevated failure buckets in the current snapshot.</p>
      ) : null}
      <div className="mt-5 border-t border-stroke/80 pt-4">
        <DeadLetterPanel items={deadLetterItems} loading={Boolean(loading)} apiBase={apiBase} embedded />
      </div>
    </ControlPlaneSection>
  );
}
