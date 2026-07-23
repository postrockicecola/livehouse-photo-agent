import { NextRequest } from "next/server";
import { isReadOnlyGalleryDeploy, proxyGalleryApi, showcaseReadOnlyJson } from "@/lib/galleryApiProxy";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    return showcaseReadOnlyJson({ profile: null, active: false });
  }
  try {
    return await proxyGalleryApi(req, "api/gallery/taste");
  } catch {
    return showcaseReadOnlyJson({ profile: null, active: false });
  }
}
