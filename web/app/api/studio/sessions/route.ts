import { NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase, loadFixture } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  if (isShowcase()) {
    return NextResponse.json(loadFixture("studio-sessions"));
  }
  try {
    const { searchParams } = new URL(req.url);
    const raw = Number(searchParams.get("limit"));
    const limit = Number.isFinite(raw) ? Math.min(500, Math.max(1, Math.floor(raw))) : 500;
    const data = await runStudioCli<Record<string, unknown>>("sessions", ["--limit", String(limit)]);
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(loadFixture("studio-sessions"));
  }
}
