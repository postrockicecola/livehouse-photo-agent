import { JobTimeline } from "@/components/JobTimeline";
import { AppNav } from "@/components/ui/AppNav";
import { isLandingOnly, isShowcase } from "@/lib/dataSource";
import { loadInfraFixturePayload } from "@/lib/infraFixtureApi";

export const dynamic = "force-dynamic";

type TimelineFallback = NonNullable<ReturnType<typeof loadInfraFixturePayload>>;

export default function InfraJobDetailPage({ params }: { params: { jobId: string } }) {
  const jobId = params.jobId;
  const fallbackData = loadInfraFixturePayload(["jobs", jobId, "timeline"]) as TimelineFallback | null;
  // Prefer the always-on showcase route so portfolio deploys never depend on
  // `/api/infra/*` proxy/env wiring for walkthrough drill-downs.
  const apiPath =
    isShowcase() || isLandingOnly()
      ? `/api/showcase/infra/jobs/${encodeURIComponent(jobId)}/timeline`
      : `/api/infra/jobs/${encodeURIComponent(jobId)}/timeline`;

  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="px-4 py-4 sm:px-6">
        <JobTimeline
          apiPath={apiPath}
          fallbackData={fallbackData}
          title="Job · Trace timeline"
          backHref="/infra"
        />
      </main>
    </div>
  );
}
