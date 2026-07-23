import { NextRequest, NextResponse } from "next/server";
import { isReadOnlyGalleryDeploy, proxyGalleryApi, showcaseReadOnlyJson } from "@/lib/galleryApiProxy";

export const dynamic = "force-dynamic";

const EMPTY = {
  active: false,
  session_vibe: null as null,
  previews_dir: "/showcase/agent-demo",
};

export async function GET(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    return showcaseReadOnlyJson({ ...EMPTY });
  }
  try {
    return await proxyGalleryApi(req, "api/vibe/session");
  } catch {
    return showcaseReadOnlyJson({ ...EMPTY });
  }
}

export async function PUT(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    let body: { prompt?: string; clear?: boolean } = {};
    try {
      body = (await req.json()) as { prompt?: string; clear?: boolean };
    } catch {
      body = {};
    }
    if (body.clear || !String(body.prompt || "").trim()) {
      return showcaseReadOnlyJson({ ...EMPTY, cleared: true });
    }
    // Soft-ack so Studio Agent / Gallery vibe UI can continue; CSS grade is client-side.
    const prompt = String(body.prompt || "").trim();
    return showcaseReadOnlyJson({
      active: true,
      previews_dir: "/showcase/agent-demo",
      session_vibe: {
        prompt,
        film_variant: "cinestill_800t",
        label_zh: "Showcase 风格（CSS 模拟）",
        reason_zh: "只读部署：无光学 Lab，预览请用策展助手「打开风格预览」。",
        matched_by: "showcase:soft",
        matched: true,
      },
      persisted: false,
    });
  }
  try {
    return await proxyGalleryApi(req, "api/vibe/session");
  } catch (e) {
    return NextResponse.json(
      { detail: e instanceof Error ? e.message : "vibe unavailable" },
      { status: 502 },
    );
  }
}
