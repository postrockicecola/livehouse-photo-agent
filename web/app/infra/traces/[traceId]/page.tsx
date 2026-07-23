import { JobTimeline } from "@/components/JobTimeline";
import { AppNav } from "@/components/ui/AppNav";

/**
 * Resolves ``trace_id`` to the lowest job id in that trace (anchor) and shows the same
 * payload as the per-job timeline (plus trace-wide job list in the response).
 */
export default function InfraTraceDetailPage({ params }: { params: { traceId: string } }) {
  const short =
    params.traceId.length > 18
      ? `${params.traceId.slice(0, 10)}…${params.traceId.slice(-6)}`
      : params.traceId;

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="px-4 py-4 sm:px-6">
        <JobTimeline
          apiPath={`/api/infra/traces/${encodeURIComponent(params.traceId)}`}
          title={`Trace · ${short}`}
          backHref="/infra"
        />
      </main>
    </div>
  );
}
