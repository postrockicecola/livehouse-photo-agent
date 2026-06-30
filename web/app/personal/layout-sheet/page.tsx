"use client";

import Link from "next/link";
import { PhotoSheetEditor } from "@/components/personal/PhotoSheetEditor";
import { PersonalChrome } from "@/components/personal/PersonalChrome";

export default function PersonalLayoutSheetPage() {
  return (
    <main className="studio-grain relative flex min-h-screen flex-col px-4 py-10 sm:px-8">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(56,189,248,0.08),transparent)]" />

      <PersonalChrome title="图片排版" />

      <section className="relative z-10 mx-auto mt-10 w-full max-w-4xl flex-1">
        <div className="rounded-2xl border border-sky-500/15 bg-[#080a0c]/80 p-6 sm:p-8">
          <PhotoSheetEditor />
        </div>

        <p className="mt-8 text-center font-mono text-[10px] text-white/25">
          <Link href="/personal" className="text-sky-300/60 hover:text-sky-200/80">
            返回个人版功能列表
          </Link>
        </p>
      </section>
    </main>
  );
}
