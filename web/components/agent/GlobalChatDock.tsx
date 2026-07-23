"use client";

import { useEffect, useState } from "react";
import { ChatDock } from "@/components/agent/ChatDock";
import { getApiBase } from "@/lib/apiBase";

const API_BASE = getApiBase();

/**
 * Site-wide curation assistant FAB (bottom-left).
 * Reads landing hero `?q=` once to open + auto-send.
 */
export function GlobalChatDock() {
  const [initialPrompt, setInitialPrompt] = useState<string | null>(null);

  useEffect(() => {
    try {
      setInitialPrompt(new URLSearchParams(window.location.search).get("q"));
    } catch {
      setInitialPrompt(null);
    }
  }, []);

  return (
    <ChatDock
      apiBase={API_BASE}
      context="gallery"
      initialPrompt={initialPrompt}
    />
  );
}
