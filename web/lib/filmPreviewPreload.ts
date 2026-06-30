/** In-memory tracker for warmed ``/api/lab/film-render`` URLs (browser HTTP cache). */

const warmed = new Set<string>();
const queued = new Set<string>();
const pending: string[] = [];
const PRELOAD_CONCURRENCY = 8;
let inFlight = 0;

function drainPreloadQueue(): void {
  while (inFlight < PRELOAD_CONCURRENCY && pending.length > 0) {
    const u = pending.shift()!;
    queued.delete(u);
    if (warmed.has(u)) continue;
    inFlight += 1;
    const img = new Image();
    img.onload = () => {
      warmed.add(u);
      inFlight -= 1;
      drainPreloadQueue();
    };
    img.onerror = () => {
      inFlight -= 1;
      drainPreloadQueue();
    };
    img.src = u;
  }
}

export function isFilmPreviewWarmed(url: string): boolean {
  return warmed.has(url);
}

export function preloadFilmPreviewUrl(url: string | null | undefined): void {
  const u = url?.trim();
  if (!u || warmed.has(u) || queued.has(u)) return;
  queued.add(u);
  pending.push(u);
  drainPreloadQueue();
}

export function preloadFilmPreviewUrls(urls: Array<string | null | undefined>): void {
  for (const u of urls) preloadFilmPreviewUrl(u);
}
