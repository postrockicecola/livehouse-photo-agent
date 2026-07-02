/**
 * Data-source seam for the two deploy modes (one codebase, two data fidelities):
 *
 *   Local (127.0.0.1:3000) — full mode. Route handlers call `runStudioCli`
 *     (Python → live DB / archive). Showcase fixtures are only the catch-fallback.
 *   Vercel — showcase mode (`SHOWCASE_MODE=1`). No Python / Redis / FastAPI at
 *     runtime; route handlers serve the committed JSON snapshots below.
 *
 * Fixtures are imported statically (not read from disk) so they are guaranteed to
 * be traced into the serverless bundle on Vercel. Regenerate them with
 * `scripts/export_showcase.py` against full local data, then commit.
 */
import landingGallery from "@/fixtures/landing-gallery.json";
import landingInfra from "@/fixtures/landing-infra.json";
import landingBrain from "@/fixtures/landing-brain.json";
import landingStats from "@/fixtures/landing-stats.json";
import studioSessions from "@/fixtures/studio-sessions.json";
import studioStatus from "@/fixtures/studio-status.json";
import studioFeaturedFrames from "@/fixtures/studio-featured-frames.json";
import studioInfraOverview from "@/fixtures/studio-infra-overview.json";
import infraMetrics from "@/fixtures/infra-metrics.json";
import infraMetricsHistory from "@/fixtures/infra-metrics-history.json";
import infraWorkers from "@/fixtures/infra-workers.json";
import infraProviders from "@/fixtures/infra-providers.json";
import infraCost from "@/fixtures/infra-cost.json";
import infraDeadLetter from "@/fixtures/infra-dead-letter.json";
import infraRuntimeStream from "@/fixtures/infra-runtime-stream.json";
import infraBrain from "@/fixtures/infra-brain.json";
import infraAgentRuns from "@/fixtures/infra-agent-runs.json";
import infraJobs from "@/fixtures/infra-jobs.json";
import infraJobDetail from "@/fixtures/infra-job-detail.json";
import infraJobStages from "@/fixtures/infra-job-stages.json";
import infraJobTimeline from "@/fixtures/infra-job-timeline.json";
import infraTrace from "@/fixtures/infra-trace.json";

/** True when running as the read-only Vercel showcase (set `SHOWCASE_MODE=1`). */
export function isShowcase(): boolean {
  return process.env.SHOWCASE_MODE === "1" || process.env.SHOWCASE_MODE === "true";
}

const FIXTURES = {
  "landing-gallery": landingGallery,
  "landing-infra": landingInfra,
  "landing-brain": landingBrain,
  "landing-stats": landingStats,
  "studio-sessions": studioSessions,
  "studio-status": studioStatus,
  "studio-featured-frames": studioFeaturedFrames,
  "studio-infra-overview": studioInfraOverview,
  "infra-metrics": infraMetrics,
  "infra-metrics-history": infraMetricsHistory,
  "infra-workers": infraWorkers,
  "infra-providers": infraProviders,
  "infra-cost": infraCost,
  "infra-dead-letter": infraDeadLetter,
  "infra-runtime-stream": infraRuntimeStream,
  "infra-brain": infraBrain,
  "infra-agent-runs": infraAgentRuns,
  "infra-jobs": infraJobs,
  "infra-job-detail": infraJobDetail,
  "infra-job-stages": infraJobStages,
  "infra-job-timeline": infraJobTimeline,
  "infra-trace": infraTrace,
} as const;

export type FixtureName = keyof typeof FIXTURES;

/** Return the committed snapshot for `name`. Used both in showcase mode and as the
 *  catch-fallback in full mode, so every fixture is exercised by the real code path. */
export function loadFixture<T>(name: FixtureName): T {
  return FIXTURES[name] as unknown as T;
}
