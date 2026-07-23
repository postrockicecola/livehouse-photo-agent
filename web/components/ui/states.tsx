"use client";

import type { ReactNode } from "react";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0a0a]";

type LoadingStateProps = {
  label?: string;
  className?: string;
  bare?: boolean;
};

export function LoadingState({
  label = "加载中…",
  className = "",
  bare = false,
}: LoadingStateProps) {
  if (bare) {
    return (
      <p className={`text-sm text-white/35 ${className}`} role="status" aria-live="polite">
        {label}
      </p>
    );
  }

  return (
    <div
      className={`flex flex-col gap-3 px-6 py-10 ${className}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="h-3 w-28 animate-pulse rounded bg-white/[0.06]" />
      <div className="h-24 w-full max-w-xl animate-pulse rounded-lg bg-white/[0.04]" />
      <div className="h-3 w-48 animate-pulse rounded bg-white/[0.05]" />
      <p className="text-sm text-white/35">{label}</p>
    </div>
  );
}

type ErrorStateProps = {
  title?: string;
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
};

export function ErrorState({
  title = "出错了",
  message,
  onRetry,
  retryLabel = "重试",
  className = "",
}: ErrorStateProps) {
  return (
    <div
      className={`rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-4 py-3 ${className}`}
      role="alert"
    >
      <p className="text-[12px] font-medium text-rose-100/90">{title}</p>
      <p className="mt-1 font-mono text-[11px] leading-relaxed text-rose-200/80">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className={`mt-3 rounded-md border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[12px] text-white/75 transition-colors hover:bg-white/[0.08] hover:text-white ${focusRing}`}
        >
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}

type EmptyStateProps = {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
};

export function EmptyState({ title, description, action, className = "" }: EmptyStateProps) {
  return (
    <div className={`mx-auto flex max-w-lg flex-col items-center gap-3 px-6 py-16 text-center ${className}`}>
      <p className="text-[13px] font-medium text-white/70">{title}</p>
      {description ? (
        <div className="text-[12px] leading-relaxed text-white/40">{description}</div>
      ) : null}
      {action}
    </div>
  );
}

type PanelProps = {
  children: ReactNode;
  className?: string;
  padded?: boolean;
};

export function Panel({ children, className = "", padded = true }: PanelProps) {
  return (
    <section
      className={`rounded-xl border border-white/[0.08] bg-white/[0.02] ${padded ? "p-4 sm:p-5" : ""} ${className}`}
    >
      {children}
    </section>
  );
}
