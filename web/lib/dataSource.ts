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

/** True when running as the read-only Vercel showcase (set `SHOWCASE_MODE=1`). */
export function isShowcase(): boolean {
  return process.env.SHOWCASE_MODE === "1" || process.env.SHOWCASE_MODE === "true";
}

const FIXTURES = {
  "landing-gallery": landingGallery,
  "landing-infra": landingInfra,
  "landing-brain": landingBrain,
  "landing-stats": landingStats,
} as const;

export type FixtureName = keyof typeof FIXTURES;

/** Return the committed snapshot for `name`. Used both in showcase mode and as the
 *  catch-fallback in full mode, so every fixture is exercised by the real code path. */
export function loadFixture<T>(name: FixtureName): T {
  return FIXTURES[name] as unknown as T;
}
