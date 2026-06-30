import "./globals.css";
import type { Metadata } from "next";
import { ScrollToTop } from "@/components/ScrollToTop";

export const metadata: Metadata = {
  title: "Luma",
  description: "Luma 摄影助手 — 专业版选片修图 · 个人版轻量处理",
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
