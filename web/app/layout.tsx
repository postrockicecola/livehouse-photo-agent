import "./globals.css";
import type { Metadata } from "next";
import { ScrollToTop } from "@/components/ScrollToTop";

export const metadata: Metadata = {
  title: "Luma — AI 全栈 / Infra / Agent 项目",
  description:
    "Go 入库 + Python 多阶段推理 + Next.js 工作台。含作业队列、Worker 恢复，以及 ReAct 选片 Agent。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        {children}
        <ScrollToTop />
      </body>
    </html>
  );
}
