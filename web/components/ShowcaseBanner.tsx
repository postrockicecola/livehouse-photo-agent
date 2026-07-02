"use client";

import { isShowcaseClient } from "@/lib/showcase";

/**
 * Thin notice for the read-only Vercel deploy: the data is a real committed
 * snapshot (no live backend / GPU), so write actions are disabled. Renders
 * nothing when not in showcase mode (e.g. local full-stack runs).
 */
export function ShowcaseBanner() {
  if (!isShowcaseClient()) return null;
  return (
    <div className="border-b border-amber-400/20 bg-amber-400/[0.07] px-6 py-1.5 text-center text-[11px] text-amber-200/80">
      只读演示 · 真实数据快照（无实时后端 / GPU），切换场次、分析、重试等写操作已禁用
    </div>
  );
}
