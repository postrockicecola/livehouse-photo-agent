/** SSOT stores ``brain@<celery nodename>``; UI shows the nodename segment only. */
export function displayWorkerName(raw?: string | null, fallback = "—"): string {
  const s = (raw ?? "").trim();
  if (!s) return fallback;
  return s.startsWith("brain@") ? s.slice("brain@".length) : s;
}
