import { JobTimeline } from "@/components/JobTimeline";
import { AppNav } from "@/components/ui/AppNav";

export const dynamic = "force-dynamic";

export default function InfraJobDetailPage({ params }: { params: { jobId: string } }) {
  return (
    <div className="min-h-screen">
      <AppNav />
      <main className="px-4 py-4 sm:px-6">
        <JobTimeline
          apiPath={`/api/infra/jobs/${encodeURIComponent(params.jobId)}/timeline`}
          title="Job · Trace timeline"
          backHref="/infra"
        />
      </main>
    </div>
  );
}
