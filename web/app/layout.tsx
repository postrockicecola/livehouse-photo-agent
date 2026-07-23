import "./globals.css";
import type { Metadata } from "next";
import { IBM_Plex_Mono, Outfit } from "next/font/google";
import { GlobalChatDock } from "@/components/agent/GlobalChatDock";
import { ScrollToTop } from "@/components/ScrollToTop";

const sans = Outfit({
  subsets: ["latin"],
  variable: "--font-luma-sans",
  display: "swap",
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-luma-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Luma — AI 全栈 / Infra / Agent 项目",
  description:
    "Go 入库 + Python 多阶段推理 + Next.js 工作台。含作业队列、Worker 恢复，以及 ReAct 选片 Agent。",
  icons: {
    icon: [{ url: "/brand/luma-icon.png", type: "image/png", sizes: "any" }],
    apple: [{ url: "/brand/luma-icon.png", type: "image/png" }],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${sans.variable} ${mono.variable}`}>
      <body>
        {children}
        <GlobalChatDock />
        <ScrollToTop />
      </body>
    </html>
  );
}
