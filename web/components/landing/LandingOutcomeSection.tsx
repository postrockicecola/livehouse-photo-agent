"use client";

import Link from "next/link";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { RECORDED_OUTCOME } from "@/lib/showcaseWalkthrough";

const BEFORE_IMAGES = [
  "/demo/demo-01.jpg",
  "/demo/demo-02.jpg",
  "/demo/demo-03.jpg",
  "/demo/demo-04.jpg",
  "/demo/demo-05.jpg",
  "/demo/demo-06.jpg",
  "/demo/demo-07.jpg",
  "/demo/demo-08.jpg",
  "/demo/demo-09.jpg",
];

const AFTER_IMAGES = [
  { src: "/demo/demo-01.jpg", score: "8.7" },
  { src: "/demo/demo-03.jpg", score: "8.3" },
  { src: "/demo/demo-06.jpg", score: "8.1" },
];

function formatNum(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

export function LandingOutcomeSection() {
  const o = RECORDED_OUTCOME;

  return (
    <section
      id="outcome"
      className="landing-section scroll-mt-24 border-t border-white/[0.05] py-24 sm:py-28"
      aria-labelledby="landing-outcome-title"
    >
      <div className="mx-auto w-full max-w-[104rem] px-5 sm:px-8 lg:px-12">
        <header className="max-w-2xl">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">一次运行</p>
            <ProvenanceBadge kind="recorded" />
          </div>
          <h2
            id="landing-outcome-title"
            className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl"
          >
            从一场现场到可交付选片。
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-white/38 sm:text-base">
            代表性 Recorded Run（job #{o.jobId}）。规模统计在下方；这里先看一次作业的结果形状。
          </p>
        </header>

        <dl className="mt-12 grid grid-cols-2 gap-6 sm:grid-cols-4 sm:gap-8">
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/30">Photos in</dt>
            <dd className="mt-2 text-3xl font-light tabular-nums text-white/90">{formatNum(o.photosIn)}</dd>
            <p className="mt-1 text-xs text-white/30">会话输入规模（示意）</p>
          </div>
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/30">VLM calls</dt>
            <dd className="mt-2 text-3xl font-light tabular-nums text-white/90">{formatNum(o.vlmCalls)}</dd>
            <p className="mt-1 text-xs text-white/30">账本 model_runs</p>
          </div>
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/30">Keep rate</dt>
            <dd className="mt-2 text-3xl font-light tabular-nums text-white/90">{o.keepRatePct}%</dd>
            <p className="mt-1 text-xs text-white/30">归档平均入选率</p>
          </div>
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/30">E2E</dt>
            <dd className="mt-2 text-3xl font-light tabular-nums text-white/90">{o.e2eMinutes} min</dd>
            <p className="mt-1 text-xs text-white/30">job #{o.jobId} 墙钟时间</p>
          </div>
        </dl>

        <div className="mt-14 grid gap-8 lg:grid-cols-2 lg:gap-12">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/28">Before · 原始场次</p>
            <div className="mt-3 grid grid-cols-3 gap-1.5">
              {BEFORE_IMAGES.map((src) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={src}
                  src={src}
                  alt=""
                  className="aspect-[3/2] h-full w-full object-cover opacity-80"
                />
              ))}
            </div>
            <p className="mt-2 text-[11px] text-white/28">密集连拍与相似构图，尚未筛选。</p>
          </div>
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/28">After · 入选预览</p>
            <div className="mt-3 grid grid-cols-3 gap-2">
              {AFTER_IMAGES.map((item) => (
                <figure key={item.src} className="relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.src} alt="" className="aspect-[3/2] w-full object-cover" />
                  <figcaption className="absolute bottom-1.5 left-1.5 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-white/85">
                    {item.score}
                  </figcaption>
                </figure>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-white/28">
              底图为打包 demo 素材；分数字段为产品示意叠加（Simulated overlay）。
            </p>
          </div>
        </div>

        <div className="mt-10 flex flex-wrap items-center gap-3">
          <Link
            href="/infra?tour=1"
            className="inline-flex rounded-full bg-white px-5 py-2.5 font-mono text-[10px] uppercase tracking-[0.14em] text-[#0a0a0a] transition-opacity hover:opacity-90"
          >
            打开作业 walkthrough
          </Link>
          <Link
            href="/gallery"
            className="inline-flex rounded-full border border-white/20 px-5 py-2.5 font-mono text-[10px] uppercase tracking-[0.14em] text-white/70 transition-colors hover:border-white/35 hover:text-white"
          >
            打开 Gallery
          </Link>
        </div>
        <p className="mt-4 max-w-2xl text-[11px] leading-relaxed text-white/28">{o.notes}</p>
      </div>
    </section>
  );
}
