"use client";

import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { isShowcaseClient } from "@/lib/showcase";

/**
 * Thin notice for the read-only Vercel deploy: the data is a real committed
 * snapshot (no live backend / GPU), so write actions are disabled. Renders
 * nothing when not in showcase mode (e.g. local full-stack runs).
 */
export function ShowcaseBanner() {
  if (!isShowcaseClient()) return null;
  return (
    <div className="flex flex-wrap items-center justify-center gap-2 border-b border-amber-400/20 bg-amber-400/[0.07] px-6 py-1.5 text-center text-[11px] text-amber-200/80">
      <ProvenanceBadge kind="showcase" />
      <span>只读演示 · 已提交的真实运行快照（无实时后端 / GPU），写操作已禁用</span>
    </div>
  );
}
