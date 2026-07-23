"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import {
  INFRA_TOUR_STEPS,
  SHOWCASE_FALLBACK_JOB_ID,
  SHOWCASE_SUCCESS_JOB_ID,
  WALKTHROUGH_CASES,
  type TourStepId,
} from "@/lib/showcaseWalkthrough";
import { resolveClientProvenance } from "@/lib/provenance";

type Props = {
  onExpandJob?: (jobId: number) => void;
  /** When true, auto-open the tour (e.g. ?tour=1). */
  autoStart?: boolean;
};

export function InfraGuidedTour({ onExpandJob, autoStart = false }: Props) {
  const [open, setOpen] = useState(autoStart);
  const [step, setStep] = useState(0);
  const provenance = resolveClientProvenance();
  const current = INFRA_TOUR_STEPS[step];

  const focusStep = useCallback(
    (id: TourStepId, index: number) => {
      setStep(index);
      setOpen(true);
      const def = INFRA_TOUR_STEPS[index];
      if (!def) return;
      if (id === "success-job" || id === "model-calls") {
        onExpandJob?.(SHOWCASE_SUCCESS_JOB_ID);
      } else if (id === "fallback-job") {
        onExpandJob?.(SHOWCASE_FALLBACK_JOB_ID);
      }
      requestAnimationFrame(() => {
        document.getElementById(def.targetId)?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    },
    [onExpandJob],
  );

  useEffect(() => {
    if (autoStart) focusStep(INFRA_TOUR_STEPS[0].id, 0);
  }, [autoStart, focusStep]);

  return (
    <section
      id="infra-guided-tour"
      className="rounded-2xl border border-stroke bg-zinc-950/50 p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-600">Walkthrough</p>
            <ProvenanceBadge kind={provenance === "live" ? "recorded" : provenance} />
          </div>
          <h2 className="mt-0.5 text-lg font-semibold tracking-tight text-zinc-100 sm:text-xl">
            作业演示路径
          </h2>
          <p className="mt-1 max-w-2xl text-[11px] leading-snug text-zinc-600">
            约五分钟：成功作业 → 模型调用 → 降级恢复 → Gallery → 吞吐与成本。
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            if (open) setOpen(false);
            else focusStep(INFRA_TOUR_STEPS[0].id, 0);
          }}
          className="shrink-0 rounded-lg border border-zinc-700 bg-zinc-900/80 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-400 transition-colors hover:border-zinc-500 hover:text-zinc-200"
          aria-expanded={open}
        >
          {open ? "收起引导" : "开始引导"}
        </button>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {WALKTHROUGH_CASES.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => {
              onExpandJob?.(c.jobId);
              document.getElementById("tour-jobs")?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
            className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-3 text-left transition-colors hover:border-zinc-600"
          >
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-500">
              job #{c.jobId} · {c.title}
            </p>
            <p className="mt-1 text-sm text-zinc-200">{c.summary}</p>
            <p className="mt-1 text-[11px] text-zinc-500">{c.detail}</p>
          </button>
        ))}
      </div>

      {open && current ? (
        <div className="mt-4 rounded-xl border border-sky-500/20 bg-sky-950/20 p-3 sm:p-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-sky-400/80">
            Step {step + 1} / {INFRA_TOUR_STEPS.length}
          </p>
          <h3 className="mt-1 text-base font-medium text-zinc-100">{current.title}</h3>
          <p className="mt-1 text-sm leading-relaxed text-zinc-400">{current.body}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={step <= 0}
              onClick={() => focusStep(INFRA_TOUR_STEPS[step - 1].id, step - 1)}
              className="rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-400 disabled:opacity-40"
            >
              上一步
            </button>
            <button
              type="button"
              onClick={() => {
                if (step >= INFRA_TOUR_STEPS.length - 1) {
                  setOpen(false);
                  return;
                }
                focusStep(INFRA_TOUR_STEPS[step + 1].id, step + 1);
              }}
              className="rounded-md border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs text-sky-200"
            >
              {step >= INFRA_TOUR_STEPS.length - 1 ? "完成" : "下一步"}
            </button>
            {current.href ? (
              <Link href={current.href} className="text-xs text-sky-400 hover:underline">
                打开 {current.title} →
              </Link>
            ) : null}
          </div>
        </div>
      ) : null}

      <p id="tour-gallery-cta" className="mt-3 text-[11px] text-zinc-600">
        交付面：{" "}
        <Link href="/gallery" className="text-zinc-400 underline-offset-2 hover:text-zinc-200 hover:underline">
          Gallery
        </Link>
        {" · "}
        <Link href="/studio" className="text-zinc-400 underline-offset-2 hover:text-zinc-200 hover:underline">
          Studio
        </Link>
      </p>
    </section>
  );
}
