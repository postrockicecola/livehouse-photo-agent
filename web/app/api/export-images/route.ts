import { NextRequest, NextResponse } from "next/server";

const GALLERY_ORIGIN = (process.env.GALLERY_API_ORIGIN || "http://127.0.0.1:8080").replace(/\/$/, "");

/** Batch film export can run many minutes (RAW develop + grade); avoid dev rewrite/proxy cutting the connection. */
export const maxDuration = 600;

export async function POST(req: NextRequest) {
  const body = await req.text();
  const contentType = req.headers.get("content-type") || "application/json";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10 * 60 * 1000);
  try {
    const res = await fetch(`${GALLERY_ORIGIN}/api/export-images`, {
      method: "POST",
      headers: { "Content-Type": contentType },
      body,
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await res.text();
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("application/json")) {
      const snippet = text.replace(/\s+/g, " ").trim().slice(0, 240);
      return NextResponse.json(
        {
          success: false,
          error: `gallery_server 返回非 JSON（HTTP ${res.status}）`,
          detail: snippet || null,
        },
        { status: res.status >= 400 ? res.status : 502 },
      );
    }
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "export proxy failed";
    const aborted = e instanceof Error && e.name === "AbortError";
    return NextResponse.json(
      {
        success: false,
        error: aborted
          ? "导出超时（超过 10 分钟），请减少选中张数或关闭 RAW 显影导出"
          : `无法连接 gallery_server：${msg}`,
      },
      { status: aborted ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}
