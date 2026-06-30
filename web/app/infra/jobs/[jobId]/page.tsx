import { JobTimeline } from "@/components/JobTimeline";

export default function InfraJobDetailPage({ params }: { params: { jobId: string } }) {
  return (
    <main className="min-h-screen px-4 py-4 sm:px-6">
      <JobTimeline
        apiPath={`/api/infra/jobs/${encodeURIComponent(params.jobId)}/timeline`}
        title="Job · Trace timeline"
        backHref="/infra"
      />
    </main>
  );
}
