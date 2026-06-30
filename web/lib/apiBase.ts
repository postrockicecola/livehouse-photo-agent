/**
 * Gallery/Lab talks to FastAPI. When empty, use same-origin paths so Next.js rewrites proxy to FastAPI
 * (`next.config.js` → GALLERY_API_ORIGIN, default http://127.0.0.1:8080).
 */
export function getApiBase(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE;
  if (typeof raw === "string" && raw.trim() !== "") {
    return raw.replace(/\/$/, "");
  }
  return "";
}
