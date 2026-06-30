import { NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const raw = Number(searchParams.get("limit"));
    const limit = Number.isFinite(raw) ? Math.min(500, Math.max(1, Math.floor(raw))) : 500;
    const data = await runStudioCli<Record<string, unknown>>("sessions", ["--limit", String(limit)]);
    return NextResponse.json(data);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "studio sessions failed";
    return NextResponse.json({ detail: msg }, { status: 500 });
  }
}
