"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChatDock } from "@/components/agent/ChatDock";
import { getApiBase } from "@/lib/apiBase";
import {
  STUDIO_FIND_PROMPTS,
  STUDIO_SELECT_PROMPTS,
  STUDIO_STYLE_PROMPTS,
} from "@/lib/productIa";

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
 * On Studio: open by default and scroll the same preset prompts as the landing hero.
 * Film-vibe tools applied off-/gallery navigate to Gallery for graded preview.
 */
export function GlobalChatDock() {
  const [initialPrompt, setInitialPrompt] = useState<string | null>(null);
  const pathname = usePathname();
  const router = useRouter();
  const onStudio = Boolean(pathname?.startsWith("/studio"));

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
      // Showcase Studio opens CSS grade locally — don't bounce to /gallery.
      if (detail?.metadata?.showcase) return;
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
      key={onStudio ? "studio" : "gallery"}
      apiBase={API_BASE}
      context={onStudio ? "studio" : "gallery"}
      initialPrompt={initialPrompt}
      defaultOpen={onStudio || Boolean(initialPrompt?.trim())}
      promptStages={
        onStudio
          ? {
              select: STUDIO_SELECT_PROMPTS,
              style: STUDIO_STYLE_PROMPTS,
              find: STUDIO_FIND_PROMPTS,
            }
          : undefined
      }
    />
  );
}
