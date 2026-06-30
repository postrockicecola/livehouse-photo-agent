import { JobTimeline } from "@/components/JobTimeline";

/**
 * Resolves ``trace_id`` to the lowest job id in that trace (anchor) and shows the same
 * payload as the per-job timeline (plus trace-wide job list in the response).
 */
export default function InfraTraceDetailPage({ params }: { params: { traceId: string } }) {
  return (
    <main className="min-h-screen px-4 py-4 sm:px-6">
      <JobTimeline
        apiPath={`/api/infra/traces/${encodeURIComponent(params.traceId)}`}
        title={`Trace · ${params.traceId}`}
        backHref="/infra"
      />
    </main>
  );
}
