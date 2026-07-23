import { NextRequest, NextResponse } from "next/server";
import { isReadOnlyGalleryDeploy, proxyGalleryApi, showcaseReadOnlyJson } from "@/lib/galleryApiProxy";

export const dynamic = "force-dynamic";

const EMPTY_GET = {
  active: false,
  curation: null as null,
  previews_dir: "/showcase/agent-demo",
};

export async function GET(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    return showcaseReadOnlyJson({ ...EMPTY_GET });
  }
  try {
    return await proxyGalleryApi(req, "api/gallery/curation");
  } catch {
    return showcaseReadOnlyJson({ ...EMPTY_GET });
  }
}

/** Accept local selection saves in showcase so the UI autosave does not hard-fail. */
export async function PUT(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    let body: Record<string, unknown> = {};
    try {
      body = (await req.json()) as Record<string, unknown>;
    } catch {
      body = {};
    }
    if (body.clear) {
      return showcaseReadOnlyJson({ ...EMPTY_GET, cleared: true });
    }
    return showcaseReadOnlyJson({
      active: true,
      previews_dir: "/showcase/agent-demo",
      persisted: false,
      note: "Showcase 只读：选片仅保存在本浏览器会话内。",
      curation: {
        selected_keys: Array.isArray(body.selected_keys) ? body.selected_keys : [],
        feedback_by_key: body.feedback_by_key ?? {},
        export_by_file: body.export_by_file ?? {},
      },
    });
  }
  try {
    return await proxyGalleryApi(req, "api/gallery/curation");
  } catch (e) {
    return NextResponse.json(
      { detail: e instanceof Error ? e.message : "curation unavailable" },
      { status: 502 },
    );
  }
}
