/**
 * Client-safe showcase flag. Kept separate from `dataSource.ts` (which statically
 * imports every JSON fixture) so client components can read the flag without
 * bundling the fixtures into the browser. Set `NEXT_PUBLIC_SHOWCASE_MODE=1` on
 * the read-only Vercel deploy.
 */
export function isShowcaseClient(): boolean {
  return process.env.NEXT_PUBLIC_SHOWCASE_MODE === "1" || process.env.NEXT_PUBLIC_SHOWCASE_MODE === "true";
}
