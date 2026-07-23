import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin } from "@/lib/studioPyRunner";
import { isShowcase } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

const DEMO_IMAGE_COUNT = 12; // web/public/demo/demo-01.jpg … demo-12.jpg

/** Bundled per-session heroes only: session-NN.jpg / session-NN-portrait.jpg */
const SHOWCASE_COVER_FILE_RE = /^session-\d{2,}(?:-portrait)?\.jpg$/i;
const SHOWCASE_AGENT_DEMO_RE = /^frame-\d{2}\.jpg$/i;

/** Deterministically map an arbitrary `path` to one of the bundled demo photos. */
function demoImageFor(path: string): string {
  let hash = 0;
  for (let i = 0; i < path.length; i += 1) {
    hash = (hash * 31 + path.charCodeAt(i)) >>> 0;
  }
  const n = (hash % DEMO_IMAGE_COUNT) + 1;
  return `/demo/demo-${String(n).padStart(2, "0")}.jpg`;
}

function decodePath(path: string): string {
  try {
    return decodeURIComponent(path.trim());
  } catch {
    return path.trim();
  }
}

/** Resolve a showcase cover token to its static public URL, or null. */
function showcaseCoverFor(path: string): string | null {
  const decoded = decodePath(path);
  // Reject traversal / odd separators; only allow the opaque cover filename.
  if (!decoded || decoded.includes("..") || decoded.includes("\\") || decoded.includes("\0")) {
    return null;
  }
  const normalized = decoded.replace(/^\/+/, "").replace(/^showcase\/covers\//i, "");
  const file = normalized.includes("/") ? normalized.split("/").pop() || "" : normalized;
  if (!SHOWCASE_COVER_FILE_RE.test(file)) return null;
  return `/showcase/covers/${file}`;
}

/** Agent / Gallery showcase keepers under ``public/showcase/agent-demo/``. */
function showcaseAgentDemoFor(path: string): string | null {
  const decoded = decodePath(path);
  if (!decoded || decoded.includes("..") || decoded.includes("\\") || decoded.includes("\0")) {
    return null;
  }
  if (decoded.startsWith("/showcase/agent-demo/")) {
    const file = decoded.slice("/showcase/agent-demo/".length);
    if (SHOWCASE_AGENT_DEMO_RE.test(file) && !file.includes("/")) return decoded;
  }
  const normalized = decoded.replace(/^\/+/, "").replace(/^showcase\/agent-demo\//i, "");
  if (SHOWCASE_AGENT_DEMO_RE.test(normalized) && !normalized.includes("/")) {
    return `/showcase/agent-demo/${normalized}`;
  }
  return null;
}

/**
 * Gallery image proxy. Locally this is normally served by a `next.config.js`
 * rewrite to FastAPI, but a Route Handler wins over rewrites, so we must also
 * proxy in full mode. In showcase mode there is no backend / real archive, so
 * we redirect to an EXIF-stripped bundled demo image.
 */
export async function GET(req: NextRequest) {
  const path = req.nextUrl.searchParams.get("path") ?? "";

  if (isShowcase()) {
    const agent = showcaseAgentDemoFor(path);
    if (agent) return NextResponse.redirect(new URL(agent, req.url));
    const cover = showcaseCoverFor(path);
    return NextResponse.redirect(new URL(cover ?? demoImageFor(path), req.url));
  }

  try {
    const upstream = `${galleryApiOrigin()}/image?${req.nextUrl.searchParams.toString()}`;
    const res = await fetch(upstream, { cache: "no-store" });
    return new NextResponse(res.body, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") ?? "image/jpeg",
        "cache-control": res.headers.get("cache-control") ?? "no-store",
      },
    });
  } catch {
    return NextResponse.redirect(new URL(demoImageFor(path), req.url));
  }
}
