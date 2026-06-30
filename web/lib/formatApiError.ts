/** Normalize FastAPI / Starlette error payloads for user-visible strings. */

type FastApiErrPart = { msg?: string; type?: string; loc?: unknown[] };

export function formatApiErrorDetail(
  data: unknown,
  options?: { httpStatus?: number; rawBody?: string },
): string {
  const o = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  const detail = o.detail ?? o.message ?? o.error;

  if (typeof detail === "string" && detail.trim()) return detail.trim();

  if (Array.isArray(detail)) {
    const parts = detail.map((e) => {
      if (typeof e === "string") return e;
      if (e && typeof e === "object") {
        const p = e as FastApiErrPart;
        const loc = Array.isArray(p.loc) ? p.loc.join(".") : "";
        if (typeof p.msg === "string") return loc ? `${loc}: ${p.msg}` : p.msg;
      }
      try {
        return JSON.stringify(e);
      } catch {
        return String(e);
      }
    });
    const joined = parts.filter(Boolean).join("；");
    if (joined) return joined;
  }

  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }

  const raw = (options?.rawBody ?? "").replace(/\s+/g, " ").trim();
  if (raw) return raw.slice(0, 280);

  if (options?.httpStatus != null) return `无详细说明（HTTP ${options.httpStatus}）`;
  return "无详细说明";
}
