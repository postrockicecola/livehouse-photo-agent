"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchStudioLifetimeStats,
  fetchStudioSessions,
  fetchStudioStatus,
  setActiveSession,
  startStudioAnalyze,
  type StudioLifetimeStats,
  type StudioSessionRow,
  type StudioRecentDelivery,
  type StudioStatusResponse,
} from "@/lib/studioApi";
import {
  sortStudioSessions,
  type StudioSessionSortOrder,
} from "@/lib/studioSessionSort";
import { StudioAppNav } from "@/components/studio/StudioAppNav";
import { ErrorState, LoadingState } from "@/components/ui/states";
import { ShowcaseBanner } from "@/components/ShowcaseBanner";
import { StudioCurrentSessionHero } from "@/components/studio/StudioCurrentSessionHero";
import { StudioFeaturedFrames } from "@/components/studio/StudioFeaturedFrames";
import { StudioPipelineTimeline } from "@/components/studio/StudioPipelineTimeline";
import { StudioRecentDeliveries } from "@/components/studio/StudioRecentDeliveries";
import { StudioSessionList } from "@/components/studio/StudioSessionList";
import { StudioStatsSection } from "@/components/studio/StudioStatsSection";
import { PIPELINE_DISPLAY_LABELS } from "@/lib/studioUi";

const STUDIO_SET_LIST_SORT_KEY = "livehouse.studioSetListSort";
const TERMINAL_JOB_STATUSES = new Set(["SUCCEEDED", "FAILED_PERMANENT", "DEAD_LETTERED", "CANCELLED"]);

function readSetListSortPref(): StudioSessionSortOrder {
  if (typeof window === "undefined") return "desc";
  try {
    const v = localStorage.getItem(STUDIO_SET_LIST_SORT_KEY);
    return v === "asc" ? "asc" : "desc";
  } catch {
    return "desc";
  }
}

function StudioDivider() {
  return <div className="h-px bg-white/[0.07]" aria-hidden />;
}

export default function StudioPage() {
  const [sessions, setSessions] = useState<StudioSessionRow[]>([]);
  const [recentDeliveries, setRecentDeliveries] = useState<StudioRecentDelivery[]>([]);
  const [archiveRoot, setArchiveRoot] = useState("");
  const [selected, setSelected] = useState<StudioSessionRow | null>(null);
  const [status, setStatus] = useState<StudioStatusResponse | null>(null);
  const [lifetimeStats, setLifetimeStats] = useState<StudioLifetimeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [busy, setBusy] = useState<"activate" | "analyze" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [setListSort, setSetListSort] = useState<StudioSessionSortOrder>("desc");
  const analyzeInFlightRef = useRef(false);
  const [pendingAnalyzeJobId, setPendingAnalyzeJobId] = useState<number | null>(null);

  const refreshStatus = useCallback(async (row: StudioSessionRow | null) => {
    if (!row?.previews_dir) {
      setStatus(null);
      return;
    }
    const st = await fetchStudioStatus(row.previews_dir);
    setStatus(st);
  }, []);

  useEffect(() => {
    setSetListSort(readSetListSortPref());
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchStudioSessions();
        if (cancelled) return;
        setSessions(data.sessions);
        setRecentDeliveries(data.recent_deliveries ?? []);
        setArchiveRoot(data.archive_root);
        let pick: StudioSessionRow | null = null;
        const activePd = data.active?.previews_dir;
        if (activePd) pick = data.sessions.find((s) => s.previews_dir === activePd) ?? null;
        if (!pick && data.sessions.length) pick = data.sessions[0];
        setSelected(pick);
        setError(null);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "无法加载场次");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stats = await fetchStudioLifetimeStats();
        if (!cancelled) setLifetimeStats(stats);
      } catch {
        if (!cancelled) setLifetimeStats(null);
      } finally {
        if (!cancelled) setStatsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selected?.previews_dir) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const st = await fetchStudioStatus(selected.previews_dir);
        if (!cancelled) {
          setStatus(st);
          setError(null);
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "状态刷新失败");
      }
      timer = setTimeout(tick, 3000);
    };

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [selected?.previews_dir]);

  useEffect(() => {
    setPendingAnalyzeJobId(null);
  }, [selected?.previews_dir]);

  useEffect(() => {
    if (pendingAnalyzeJobId == null) return;
    const job = status?.job;
    if (!job) return;
    // Clear optimistic lock once the session job is no longer actively running.
    // Previously we kept pending forever when status still showed an older SUCCEEDED
    // job (id mismatch → early return), which disabled「全量分析」with no feedback.
    if (job.id === pendingAnalyzeJobId) {
      if (!job.is_running || TERMINAL_JOB_STATUSES.has(job.status)) {
        setPendingAnalyzeJobId(null);
      }
      return;
    }
    if (!job.is_running) {
      setPendingAnalyzeJobId(null);
    }
  }, [status?.job, pendingAnalyzeJobId]);

  const isActive = Boolean(
    selected &&
      status?.active?.previews_dir &&
      selected.previews_dir === status.active.previews_dir,
  );

  const jobRunning = Boolean(status?.job?.is_running);
  // Only hard-lock while a job is actually running (or the POST is in flight via `busy`).
  // `pendingAnalyzeJobId` is optimistic UI only — must not permanently disable the button.
  const analyzeLocked = jobRunning;
  // Allow opening Gallery as soon as Previews exist — do not wait for full VLM JSON.
  const previewCountForGallery = Number(
    status?.session?.preview_count ?? selected?.preview_count ?? 0,
  );
  const canGallery = Boolean(
    previewCountForGallery > 0 ||
      selected?.has_analysis_results ||
      status?.session?.has_analysis_results,
  );

  const setList = useMemo(
    () => sortStudioSessions(sessions, setListSort),
    [sessions, setListSort],
  );

  const toggleSetListSort = () => {
    setSetListSort((prev) => {
      const next: StudioSessionSortOrder = prev === "desc" ? "asc" : "desc";
      try {
        localStorage.setItem(STUDIO_SET_LIST_SORT_KEY, next);
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const onActivate = async () => {
    if (!selected?.previews_dir) return;
    setBusy("activate");
    setMessage(null);
    try {
      await setActiveSession(selected.previews_dir);
      setMessage("已设为当前场次");
      const data = await fetchStudioSessions();
      setSessions(data.sessions);
      setRecentDeliveries(data.recent_deliveries ?? []);
      setArchiveRoot(data.archive_root);
      await refreshStatus(selected);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "设置失败");
    } finally {
      setBusy(null);
    }
  };

  const onAnalyze = async () => {
    if (!selected?.previews_dir) {
      setError("当前场次没有 Previews 路径，无法启动分析");
      return;
    }
    if (analyzeInFlightRef.current) return;
    if (analyzeLocked) {
      const jid = status?.job?.id;
      setMessage(jid != null ? `分析进行中 · job #${jid}，完成后可再次全量分析` : "分析进行中，请稍候");
      return;
    }
    analyzeInFlightRef.current = true;
    setBusy("analyze");
    setError(null);
    setMessage(null);
    try {
      const res = await startStudioAnalyze(selected.previews_dir, { forceFullRerun: true });
      setPendingAnalyzeJobId(res.job_id);
      setMessage(
        res.status === "already_running"
          ? `分析已在队列中 · job #${res.job_id}`
          : `已排队全量分析 · job #${res.job_id}`,
      );
      await refreshStatus(selected);
    } catch (e: unknown) {
      setPendingAnalyzeJobId(null);
      setError(e instanceof Error ? e.message : "启动分析失败");
    } finally {
      analyzeInFlightRef.current = false;
      setBusy(null);
    }
  };

  const previewCount = status?.session?.preview_count ?? selected?.preview_count ?? 0;
  const pipeline = status?.pipeline ?? {
    labels: [...PIPELINE_DISPLAY_LABELS],
    current_index: -1,
    complete: false,
    failed: false,
  };

  const selectSession = useCallback((row: StudioSessionRow) => {
    setSelected(row);
    requestAnimationFrame(() => {
      document.getElementById("studio-session-detail")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }, []);

  return (
    <div className="studio-grain relative flex min-h-[100dvh] flex-col bg-[#0e0e0e] text-[#e8e8e8]">
      <StudioAppNav />
      <ShowcaseBanner />

      {loading ? <LoadingState label="Loading sessions…" className="px-6 py-4" /> : null}

      {error ? (
        <div className="px-6 py-2">
          <ErrorState message={error} onRetry={() => window.location.reload()} />
        </div>
      ) : null}

      {message ? (
        <p className="px-6 py-2 text-xs text-[#5dcaa5]/90">{message}</p>
      ) : null}

      {selected ? (
        <div id="studio-session-detail" className="scroll-mt-[42px]">
          <StudioCurrentSessionHero
            session={selected}
            status={status}
            isActive={isActive}
            jobRunning={jobRunning}
            canGallery={canGallery}
            analyzeLocked={analyzeLocked}
            busy={busy}
            onActivate={() => void onActivate()}
            onAnalyze={() => void onAnalyze()}
          >
            <StudioPipelineTimeline
              pipeline={pipeline}
              events={status?.events ?? []}
              previewCount={previewCount}
              jobRunning={jobRunning}
            />

            <StudioStatsSection stats={lifetimeStats} loading={statsLoading} variant="kpi" bare />
          </StudioCurrentSessionHero>
        </div>
      ) : !loading && !error ? (
        <div className="relative flex h-[220px] flex-col justify-end overflow-hidden px-6 pb-6 sm:h-[300px] lg:h-[360px]">
          <div className="absolute inset-0 bg-[#14110c]" />
          <div className="relative z-[1] max-w-md">
            <p className="text-[10px] uppercase tracking-[0.12em] text-white/40">No sessions yet</p>
            <p className="mt-2 text-sm text-white/55">导入场次预览后，这里会显示当前 Session Hero。</p>
            <p className="mt-1 text-[11px] text-white/30">配置 source_dir 并运行 ingest / ANALYZE。</p>
          </div>
        </div>
      ) : null}

      <main className="flex flex-1 flex-col gap-6 px-6 py-5 pb-16 sm:px-6">
        {selected ? (
          <>
            {recentDeliveries.length > 0 ? (
              <StudioRecentDeliveries
                deliveries={recentDeliveries}
                loading={loading}
                selectedPreviewsDir={selected.previews_dir}
                onSelectSession={(pd) => {
                  const row = sessions.find((s) => s.previews_dir === pd);
                  if (row) selectSession(row);
                }}
                collapsible
                defaultCollapsed
              />
            ) : null}

            <StudioDivider />

            <StudioFeaturedFrames previewsDir={selected.previews_dir} canGallery={canGallery} />

            {status?.job ? (
              <p className="-mt-3 text-[10px] text-white/25">
                Job #{status.job.id} · {status.job.status}
                {status.job.status === "QUEUED" ? " · waiting for worker" : ""}
              </p>
            ) : null}
          </>
        ) : (
          <StudioStatsSection stats={lifetimeStats} loading={statsLoading} variant="kpi" />
        )}

        <StudioDivider />

        <StudioSessionList
          setList={setList}
          setListSort={setListSort}
          selectedPreviewsDir={selected?.previews_dir}
          activePreviewsDir={status?.active?.previews_dir}
          loading={loading}
          archiveRoot={archiveRoot}
          onSelect={selectSession}
          onToggleSort={toggleSetListSort}
        />
      </main>
    </div>
  );
}
