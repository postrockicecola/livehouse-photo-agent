"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChatDock } from "@/components/agent/ChatDock";
import { getApiBase } from "@/lib/apiBase";

const API_BASE = getApiBase();

const OPEN_VIBE_PREVIEW_KEY = "luma.open_vibe_preview";

/** Surfaces where the curation assistant is in-context. */
function showChatOnPath(pathname: string | null): boolean {
  if (!pathname) return false;
  if (pathname === "/" || pathname.startsWith("/gallery") || pathname.startsWith("/studio")) return true;
  return false;
}

/**
 * Site-wide curation assistant FAB.
 * Reads landing hero `?q=` once to open + auto-send.
 * Film-vibe tools applied off-/gallery navigate to Gallery for graded preview.
 */
export function GlobalChatDock() {
  const [initialPrompt, setInitialPrompt] = useState<string | null>(null);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    try {
      setInitialPrompt(new URLSearchParams(window.location.search).get("q"));
    } catch {
      setInitialPrompt(null);
    }
  }, []);

  useEffect(() => {
    const onAgentAction = (ev: Event) => {
      const detail = (
        ev as CustomEvent<{ action?: string; metadata?: Record<string, unknown> }>
      ).detail;
      if (String(detail?.action || "") !== "reload_vibe") return;
      const sv = detail?.metadata?.session_vibe;
      if (!sv || typeof sv !== "object") return;
      if ((pathname || "").startsWith("/gallery")) return;
      try {
        sessionStorage.setItem(OPEN_VIBE_PREVIEW_KEY, "1");
      } catch {
        /* ignore */
      }
      router.push("/gallery");
    };
    window.addEventListener("luma:gallery-agent-action", onAgentAction as EventListener);
    return () =>
      window.removeEventListener("luma:gallery-agent-action", onAgentAction as EventListener);
  }, [pathname, router]);

  if (!showChatOnPath(pathname)) return null;

  return (
    <ChatDock
      apiBase={API_BASE}
      context="gallery"
      initialPrompt={initialPrompt}
    />
  );
}
