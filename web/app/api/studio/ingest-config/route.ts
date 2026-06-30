import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin, runStudioCli } from "@/lib/studioPyRunner";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await runStudioCli<Record<string, unknown>>("ingest-config-get");
    return NextResponse.json(data);
  } catch {
    try {
      const res = await fetch(`${galleryApiOrigin()}/api/studio/ingest-config`, { cache: "no-store" });
      if (res.ok) return NextResponse.json(await res.json());
    } catch {
      /* ignore */
    }
    return NextResponse.json({ detail: "ingest-config unavailable" }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  const body = await req.text();
  try {
    const data = await runStudioCli<Record<string, unknown>>("ingest-config-put", [body]);
    return NextResponse.json(data);
  } catch {
    try {
      const res = await fetch(`${galleryApiOrigin()}/api/studio/ingest-config`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body,
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return NextResponse.json(
          { detail: typeof data.detail === "string" ? data.detail : "save failed" },
          { status: res.status },
        );
      }
      return NextResponse.json(data);
    } catch {
      return NextResponse.json({ detail: "ingest-config save failed" }, { status: 500 });
    }
  }
}
