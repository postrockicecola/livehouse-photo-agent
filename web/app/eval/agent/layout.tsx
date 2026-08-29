import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Agent Daily Eval · Livehouse Photography Agent",
  description: "Daily Agent behavior, quality, trajectory, runtime, and baseline regression dashboard.",
};

export default function AgentEvalLayout({ children }: { children: ReactNode }) {
  return children;
}
