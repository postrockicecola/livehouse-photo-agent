import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin } from "@/lib/studioPyRunner";
import { isShowcase } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

/** Prefer FastAPI ``/api/studio/analyze``; fall back to legacy ``/api/tasks/analyze``. */
export async function POST(req: NextRequest) {
  if (isShowcase()) {
    return NextResponse.json(
      { detail: "只读演示模式：Vercel 快照不运行分析任务（需本地后端 + GPU）" },
      { status: 403 },
    );
  }
  let previews_dir = "";
  let config_path = "configs/livehouse.yaml";
  let force_full_rerun = true;
  try {
    const body = (await req.json()) as {
      previews_dir?: string;
      config_path?: string;
      force_full_rerun?: boolean;
    };
    previews_dir = String(body.previews_dir || "").trim();
    if (body.config_path) config_path = String(body.config_path);
    if (typeof body.force_full_rerun === "boolean") {
      force_full_rerun = body.force_full_rerun;
    }
  } catch {
    return NextResponse.json({ detail: "invalid JSON body" }, { status: 400 });
  }
  if (!previews_dir) {
    return NextResponse.json({ detail: "previews_dir is required" }, { status: 400 });
  }

  const origin = galleryApiOrigin();
  const payload = JSON.stringify({ previews_dir, config_path, force_full_rerun });

  for (const path of ["/api/studio/analyze", "/api/tasks/analyze"]) {
    try {
      const url =
        path === "/api/tasks/analyze"
          ? `${origin}${path}?${new URLSearchParams({
              source_dir: previews_dir,
              config_path,
            })}`
          : `${origin}${path}`;
      const res = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: path === "/api/studio/analyze" ? payload : undefined,
        cache: "no-store",
      });
      if (res.status === 404) continue;
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return NextResponse.json(
          { detail: typeof data.detail === "string" ? data.detail : `analyze ${res.status}` },
          { status: res.status },
        );
      }
      return NextResponse.json(data);
    } catch {
      continue;
    }
  }

  return NextResponse.json({ detail: "analyze API unavailable (restart gallery_server?)" }, { status: 502 });
}
