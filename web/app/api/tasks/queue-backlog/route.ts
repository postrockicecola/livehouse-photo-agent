import { NextRequest } from "next/server";
import { isReadOnlyGalleryDeploy, proxyGalleryApi, showcaseReadOnlyJson } from "@/lib/galleryApiProxy";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    return showcaseReadOnlyJson({
      active: 0,
      reserved: 0,
      scheduled: 0,
      celery_unavailable: true,
    });
  }
  try {
    return await proxyGalleryApi(req, "api/tasks/queue-backlog");
  } catch {
    return showcaseReadOnlyJson({
      active: 0,
      reserved: 0,
      scheduled: 0,
      celery_unavailable: true,
    });
  }
}
