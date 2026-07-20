import type { StudioSessionRow } from "@/lib/studioApi";

export type StudioSessionSortOrder = "desc" | "asc";

/** Leading ``YYYY-MM-DD`` in session folder name; else 0. */
export function sessionDateUnix(sessionKey: string): number {
  const sk = sessionKey.trim();
  if (sk.length < 10 || sk[4] !== "-" || sk[7] !== "-") return 0;
  const y = Number(sk.slice(0, 4));
  const m = Number(sk.slice(5, 7));
  const d = Number(sk.slice(8, 10));
  if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) return 0;
  return Date.UTC(y, m - 1, d) / 1000;
}

function sessionSortUnix(row: StudioSessionRow): number {
  const explicit = row.session_date?.trim();
  if (explicit) {
    const t = sessionDateUnix(explicit);
    if (t > 0) return t;
  }
  return sessionDateUnix(row.session_key);
}

export function compareStudioSessions(
  a: StudioSessionRow,
  b: StudioSessionRow,
  order: StudioSessionSortOrder,
): number {
  const ta = sessionSortUnix(a);
  const tb = sessionSortUnix(b);
  if (ta !== tb) {
    return order === "desc" ? tb - ta : ta - tb;
  }
  return a.session_key.localeCompare(b.session_key, undefined, { sensitivity: "base" });
}

export function sortStudioSessions(
  rows: StudioSessionRow[],
  order: StudioSessionSortOrder,
): StudioSessionRow[] {
  return [...rows].sort((a, b) => compareStudioSessions(a, b, order));
}
