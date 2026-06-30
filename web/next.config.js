/** @type {import('next').NextConfig} */

/** @returns {string | null} FastAPI origin for rewrites; null = landing-only (no backend proxy). */
function resolveGalleryOrigin() {
  const landingOnly = process.env.LANDING_ONLY;
  if (landingOnly === "1" || landingOnly === "true") {
    return null;
  }
  const raw = process.env.GALLERY_API_ORIGIN;
  if (raw === "") {
    return null;
  }
  return raw || "http://127.0.0.1:8080";
}

const galleryOrigin = resolveGalleryOrigin();

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (!galleryOrigin) {
      return [];
    }
    // Browser hits :3000 only; avoids cross-port requests to FastAPI (:8080).
    // Next Route Handlers under app/api/landing/* and app/api/studio/* win over these rewrites.
    return [
      { source: "/image", destination: `${galleryOrigin}/image` },
      { source: "/analysis_results.json", destination: `${galleryOrigin}/analysis_results.json` },
      { source: "/api/:path*", destination: `${galleryOrigin}/api/:path*` },
    ];
  },
};

module.exports = nextConfig;
