import { NextRequest } from "next/server";
import { isReadOnlyGalleryDeploy, proxyGalleryApi, showcaseReadOnlyJson } from "@/lib/galleryApiProxy";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    return showcaseReadOnlyJson(
      { detail: "Showcase 只读：无法提交 ANALYZE 任务。" },
      403,
    );
  }
  try {
    return await proxyGalleryApi(req, "api/tasks/analyze");
  } catch (e) {
    return showcaseReadOnlyJson(
      { detail: e instanceof Error ? e.message : "analyze unavailable" },
      502,
    );
  }
}
