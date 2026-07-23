"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GalleryEmptyState } from "@/components/GalleryEmptyState";
import { StudioAppNav } from "@/components/studio/StudioAppNav";
import { GalleryMasonry } from "@/components/GalleryMasonry";
import { LabCompareModal } from "@/components/LabCompareModal";
import { SelectedPreviewModal } from "@/components/SelectedPreviewModal";
import type { GalleryExportItem, GalleryItem } from "@/components/types";
import { getApiBase } from "@/lib/apiBase";
import {
  catalogBasenameForExport,
  defaultFilmExportItem,
  gallerySelectionKey,
} from "@/lib/defaultFilmExport";
import { serializeExportRequestBody } from "@/lib/exportPayload";
import {
  buildCurationSavePayload,
  fetchGalleryCuration,
  hydrateFeedbackFromCuration,
  likedKeysFromFeedback,
  saveGalleryCuration,
  setFeedbackVerdict,
  toggleFeedbackLikeReason,
  type CurationFeedbackEntry,
  type CurationLikeReason,
  type GalleryCurationState,
} from "@/lib/galleryCuration";
import {
  fetchTasteProfile,
  rebuildTasteProfile,
  tasteTopHints,
  type TasteProfile,
} from "@/lib/tasteProfile";
import {
  clearSessionVibeApi,
  fetchSessionVibe,
  saveSessionVibe,
  sessionVibeMatched,
  type SessionVibeState,
} from "@/lib/sessionVibe";
import { formatApiErrorDetail } from "@/lib/formatApiError";
import { GALLERY_MASONRY_MAX_CLASS } from "@/lib/galleryLayout";
import {
  bootstrapGallery,
  fetchGalleryResultsPage,
  GALLERY_PAGE_LIMIT,
  type GalleryLoadSource,
  type GallerySort,
} from "@/lib/galleryLoad";

const API_BASE = getApiBase();
const GALLERY_BURST_DEDUPE_PREF_KEY = "livehouse.galleryBurstDedupe";
const GALLERY_SORT_TASTE_PREF_KEY = "livehouse.gallerySortPersonalized";
const GALLERY_SORT_DIVERSE_PREF_KEY = "livehouse.gallerySortDiverse";

function readGalleryDiversePref(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(GALLERY_SORT_DIVERSE_PREF_KEY) === "1";
  } catch {
    return false;
  }
}

function readGallerySortPersonalizedPref(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(GALLERY_SORT_TASTE_PREF_KEY) === "1";
  } catch {
    return false;
  }
}

function readGalleryBurstDedupePref(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem(GALLERY_BURST_DEDUPE_PREF_KEY) !== "0";
  } catch {
    return true;
  }
}

/** Safety net; first lite slice should finish well under this. */
const FETCH_TIMEOUT_MS = 30_000;
const FIRST_SCREEN_DECODE_PREFETCH = 12;
const QUEUE_BACKLOG_DEFER_MS = 2_500;

type QueueBacklogLite = {
  workers?: Array<{ worker: string }>;
  totals?: { active?: number; reserved?: number; scheduled?: number };
  redis_error?: string | null;
  celery_unavailable?: boolean;
};

function buildImageUrl(item: GalleryItem) {
  if (!item.path_quoted) return "";
  const r = Number(item.rotate_degrees ?? 0);
  return `${API_BASE}/image?path=${item.path_quoted}&max_side=900${r ? `&rotate=${r}` : ""}`;
}

export default function HomePage() {
  const [items, setItems] = useState<GalleryItem[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadingItems, setLoadingItems] = useState(true);
  const [datasetTotal, setDatasetTotal] = useState<number | null>(null);
  const [datasetTotalRaw, setDatasetTotalRaw] = useState<number | null>(null);
  const [galleryErr, setGalleryErr] = useState<string | null>(null);
  const [bootstrapErr, setBootstrapErr] = useState<string | null>(null);
  const [loadSource, setLoadSource] = useState<GalleryLoadSource>("none");
  const [reloadNonce, setReloadNonce] = useState(0);

  const [modal, setModal] = useState<GalleryItem | null>(null);
  const [selectionPreviewOpen, setSelectionPreviewOpen] = useState(false);
  /** Copilot ``gallery_search`` / vibe hits — preview without mutating liked selection. */
  const [agentPreviewItems, setAgentPreviewItems] = useState<GalleryItem[] | null>(null);
  const [agentPreviewVariant, setAgentPreviewVariant] = useState<"agent" | "vibe">("agent");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [feedbackByKey, setFeedbackByKey] = useState<Record<string, CurationFeedbackEntry>>({});
  /** ``undefined`` = not loaded; ``null`` = no file; object = apply when ``items`` ready. */
  const [pendingCuration, setPendingCuration] = useState<GalleryCurationState | null | undefined>(
    undefined,
  );
  /** Per-file JPEG export intent from Lab strip（未设置时服务端仍对预览走默认胶片导出）. */
  const [exportByFile, setExportByFile] = useState<Record<string, GalleryExportItem>>({});
  const [busy, setBusy] = useState<"export" | "enhance" | null>(null);
  const [actionMsg, setActionMsg] = useState<string>("");
  const [galleryBasePath, setGalleryBasePath] = useState<string | null>(null);
  const [queueBacklog, setQueueBacklog] = useState<QueueBacklogLite | null>(null);
  const [vibePrompt, setVibePrompt] = useState("");
  const [sessionVibe, setSessionVibe] = useState<SessionVibeState | null>(null);
  const [useSessionVibeForExport, setUseSessionVibeForExport] = useState(false);
  const [vibeBusy, setVibeBusy] = useState(false);
  const [galleryBurstDedupe, setGalleryBurstDedupe] = useState(readGalleryBurstDedupePref);
  const [gallerySortPersonalized, setGallerySortPersonalized] = useState(readGallerySortPersonalizedPref);
  const [galleryDiverse, setGalleryDiverse] = useState(readGalleryDiversePref);
  const [tasteProfile, setTasteProfile] = useState<TasteProfile | null>(null);
  const [tasteMsg, setTasteMsg] = useState("");

  const gallerySort: GallerySort = galleryDiverse
    ? "diverse"
    : gallerySortPersonalized
      ? "personalized"
      : "overall";

  const setGalleryDiversePref = useCallback((on: boolean) => {
    setGalleryDiverse(on);
    if (on) {
      setGallerySortPersonalized(false);
      try {
        localStorage.setItem(GALLERY_SORT_TASTE_PREF_KEY, "0");
      } catch {
        /* ignore */
      }
    }
    try {
      localStorage.setItem(GALLERY_SORT_DIVERSE_PREF_KEY, on ? "1" : "0");
    } catch {
      /* ignore */
    }
    setReloadNonce((n) => n + 1);
  }, []);

  const setGallerySortPersonalizedPref = useCallback((on: boolean) => {
    setGallerySortPersonalized(on);
    if (on) {
      setGalleryDiverse(false);
      try {
        localStorage.setItem(GALLERY_SORT_DIVERSE_PREF_KEY, "0");
      } catch {
        /* ignore */
      }
    }
    try {
      localStorage.setItem(GALLERY_SORT_TASTE_PREF_KEY, on ? "1" : "0");
    } catch {
      /* ignore */
    }
    setReloadNonce((n) => n + 1);
  }, []);

  const setGalleryBurstDedupePref = useCallback((on: boolean) => {
    setGalleryBurstDedupe(on);
    try {
      localStorage.setItem(GALLERY_BURST_DEDUPE_PREF_KEY, on ? "1" : "0");
    } catch {
      /* ignore */
    }
    setReloadNonce((n) => n + 1);
  }, []);

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const prefetchedRef = useRef<{ offset: number; payload: unknown } | null>(null);
  const lastLoadTsRef = useRef(0);
  const decodeQueueRef = useRef<string[]>([]);
  const decodingSetRef = useRef<Set<string>>(new Set());
  const decodePumpingRef = useRef(false);
  const curationHydratedRef = useRef(false);
  const curationSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingVibePreviewRef = useRef(false);
  /** Last observed gallery totals for mid-job progressive refresh. */
  const galleryPollTotalsRef = useRef<{ raw: number | null; count: number | null }>({
    raw: null,
    count: null,
  });
  const [curationSaveState, setCurationSaveState] = useState<"idle" | "saving" | "saved" | "err">("idle");

  const paginated = loadSource === "results_api";

  const enqueueDecode = useCallback((urls: string[]) => {
    for (const u of urls) {
      if (!u) continue;
      if (decodingSetRef.current.has(u)) continue;
      decodingSetRef.current.add(u);
      decodeQueueRef.current.push(u);
    }
    if (decodePumpingRef.current) return;
    decodePumpingRef.current = true;
    const maxParallel = 3;
    let active = 0;
    const runNext = () => {
      while (active < maxParallel && decodeQueueRef.current.length > 0) {
        const url = decodeQueueRef.current.shift()!;
        active += 1;
        const img = new window.Image();
        img.decoding = "async";
        img.src = url;
        const done = () => {
          active -= 1;
          if (decodeQueueRef.current.length === 0 && active === 0) {
            decodePumpingRef.current = false;
            return;
          }
          runNext();
        };
        void img.decode().then(done).catch(done);
      }
      if (decodeQueueRef.current.length === 0 && active === 0) {
        decodePumpingRef.current = false;
      }
    };
    runNext();
  }, []);

  useEffect(() => {
    let dead = false;
    const ctrl = new AbortController();
    let timedOut = false;
    const tid = setTimeout(() => {
      timedOut = true;
      ctrl.abort();
    }, FETCH_TIMEOUT_MS);

    const run = async () => {
      setGalleryErr(null);
      setBootstrapErr(null);
      setLoadingItems(true);
      try {
        const boot = await bootstrapGallery(ctrl.signal, {
          dedupe: galleryBurstDedupe,
          sort: gallerySort,
        });
        clearTimeout(tid);
        if (dead) return;
        setItems(boot.items);
        setNextOffset(boot.nextOffset);
        setHasMore(Boolean(boot.hasMore));
        setDatasetTotal(boot.count);
        setDatasetTotalRaw(boot.totalRaw);
        galleryPollTotalsRef.current = {
          raw: boot.totalRaw,
          count: boot.count,
        };
        setLoadSource(boot.loadSource);
        if (boot.activeBaseDir) setGalleryBasePath(boot.activeBaseDir);
        setBootstrapErr(boot.error);
        if (boot.items.length === 0 && boot.error) {
          setGalleryErr(
            timedOut
              ? `连接超时（>${FETCH_TIMEOUT_MS / 1000}s）或 API 无数据。请确认 gallery_server 已启动；可检查 GALLERY_API_ORIGIN。`
              : boot.error,
          );
        } else {
          setGalleryErr(null);
        }
        enqueueDecode(boot.items.slice(0, FIRST_SCREEN_DECODE_PREFETCH).map(buildImageUrl));
        if (boot.items.length > 0) setLoadingItems(false);
      } catch (e: unknown) {
        clearTimeout(tid);
        if (dead) return;
        const msg = e instanceof Error ? e.message : String(e);
        setGalleryErr(msg);
        setItems([]);
        setDatasetTotal(0);
        setLoadSource("none");
      } finally {
        if (!dead) setLoadingItems(false);
      }
    };
    void run();
    return () => {
      dead = true;
      clearTimeout(tid);
      ctrl.abort();
    };
  }, [reloadNonce, enqueueDecode, galleryBurstDedupe, gallerySort]);

  // Mid-job: poll gallery totals and soft-reload when new Previews / scored rows appear.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchGalleryResultsPage(
          API_BASE,
          0,
          1,
          undefined,
          galleryBurstDedupe,
          gallerySort,
        );
        if (cancelled || !data) return;
        const raw = Number(data.total_raw ?? data.count ?? 0);
        const count = Number(data.count ?? 0);
        const prev = galleryPollTotalsRef.current;
        const grew =
          (prev.raw != null && raw > prev.raw) ||
          (prev.count != null && count > prev.count) ||
          ((prev.raw == null || prev.raw === 0) && raw > 0);
        galleryPollTotalsRef.current = { raw, count };
        // Avoid reload loops when totals are unchanged after bootstrap.
        if (grew && (prev.raw !== raw || prev.count !== count)) {
          setActionMsg(`会话进行中：已发现 ${raw} 张预览，正在刷新画廊…`);
          setReloadNonce((n) => n + 1);
        }
      } catch {
        /* ignore transient poll errors */
      }
    };
    const deferId = globalThis.setTimeout(() => {
      void tick();
    }, 2500);
    const intervalId = globalThis.setInterval(() => {
      void tick();
    }, 8_000);
    return () => {
      cancelled = true;
      clearTimeout(deferId);
      clearInterval(intervalId);
    };
  }, [API_BASE, galleryBurstDedupe, gallerySort]);

  useEffect(() => {
    if (!paginated || !hasMore) return;
    const node = sentinelRef.current;
    if (!node) return;
    const io = new IntersectionObserver(
      async (entries) => {
        const en = entries[0];
        if (!en.isIntersecting) return;
        if (loadingMore || loadingItems) return;
        if (nextOffset == null) return;
        const now = Date.now();
        if (now - lastLoadTsRef.current < 500) return;
        lastLoadTsRef.current = now;
        setLoadingMore(true);
        try {
          let data: any;
          if (prefetchedRef.current?.offset === nextOffset) {
            data = prefetchedRef.current.payload;
            prefetchedRef.current = null;
          } else {
            data = await fetchGalleryResultsPage(
              API_BASE,
              nextOffset,
              GALLERY_PAGE_LIMIT,
              undefined,
              galleryBurstDedupe,
              gallerySort,
            );
          }
          const more: GalleryItem[] = data.items ?? [];
          setItems((prev) => {
            const keyOf = (it: GalleryItem, idx: number) => it.file ?? it.path ?? `item-${idx}`;
            const seen = new Set(prev.map((x, i) => keyOf(x, i)));
            return prev.concat(more.filter((x, i) => !seen.has(keyOf(x, i))));
          });
          setNextOffset(data.next_offset ?? null);
          setHasMore(Boolean(data.has_more));
          enqueueDecode(more.slice(0, 36).map(buildImageUrl));
        } catch {
          /* keep hasMore */
        } finally {
          setLoadingMore(false);
        }
      },
      { rootMargin: "600px 0px" },
    );
    io.observe(node);
    return () => io.disconnect();
  }, [API_BASE, hasMore, loadingItems, loadingMore, nextOffset, paginated, enqueueDecode, galleryBurstDedupe, gallerySort]);

  useEffect(() => {
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | undefined;
    const load = async () => {
      try {
        const q = await fetch(`${API_BASE}/api/tasks/queue-backlog`, { cache: "no-store" }).then((r) =>
          r.ok ? r.json() : null,
        );
        if (cancelled) return;
        if (q) setQueueBacklog(q);
      } catch {
        if (!cancelled) setQueueBacklog(null);
      }
    };
    const deferId = globalThis.setTimeout(() => {
      void load();
      intervalId = globalThis.setInterval(load, 15_000);
    }, QUEUE_BACKLOG_DEFER_MS);
    return () => {
      cancelled = true;
      clearTimeout(deferId);
      if (intervalId) clearInterval(intervalId);
    };
  }, [API_BASE]);

  const refreshTasteProfile = useCallback(async () => {
    try {
      const t = await fetchTasteProfile(API_BASE);
      setTasteProfile(t.profile);
      setTasteMsg(t.active ? "" : "勾选至少 5 张图并保存后，可生成「我的口味」排序");
    } catch {
      setTasteProfile(null);
    }
  }, [API_BASE]);

  useEffect(() => {
    if (!galleryBasePath) return;
    void refreshTasteProfile();
  }, [galleryBasePath, reloadNonce, refreshTasteProfile]);

  useEffect(() => {
    if (!galleryBasePath) return;
    let cancelled = false;
    curationHydratedRef.current = false;
    const load = async () => {
      try {
        const data = await fetchGalleryCuration(API_BASE);
        if (cancelled) return;
        const cur = data.curation;
        setPendingCuration(cur);
      } catch {
        /* ignore — fresh session */
        setPendingCuration(null);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [API_BASE, galleryBasePath, reloadNonce]);

  useEffect(() => {
    if (pendingCuration === undefined) return;
    if (pendingCuration === null) {
      if (!curationHydratedRef.current) {
        setFeedbackByKey({});
        setSelectedKeys(new Set());
        curationHydratedRef.current = true;
      }
      return;
    }
    const hydrated = hydrateFeedbackFromCuration(pendingCuration, items);
    setFeedbackByKey(hydrated);
    setSelectedKeys(new Set(likedKeysFromFeedback(hydrated)));
    if (pendingCuration.export_by_file && Object.keys(pendingCuration.export_by_file).length > 0) {
      setExportByFile(pendingCuration.export_by_file);
    }
    curationHydratedRef.current = true;
    setPendingCuration(undefined);
  }, [pendingCuration, items]);

  useEffect(() => {
    if (!curationHydratedRef.current || !galleryBasePath) return;
    if (curationSaveTimerRef.current) clearTimeout(curationSaveTimerRef.current);
    curationSaveTimerRef.current = setTimeout(() => {
      setCurationSaveState("saving");
      void saveGalleryCuration(API_BASE, buildCurationSavePayload(feedbackByKey, exportByFile))
        .then(() => {
          setCurationSaveState("saved");
          void refreshTasteProfile();
        })
        .catch(() => setCurationSaveState("err"));
    }, 700);
    return () => {
      if (curationSaveTimerRef.current) clearTimeout(curationSaveTimerRef.current);
    };
  }, [API_BASE, galleryBasePath, feedbackByKey, exportByFile, refreshTasteProfile]);

  useEffect(() => {
    let cancelled = false;
    const loadVibe = async () => {
      try {
        const data = await fetchSessionVibe(API_BASE);
        if (cancelled) return;
        const sv = data.session_vibe;
        if (sv?.film_variant) {
          setSessionVibe(sv);
          setVibePrompt(sv.prompt ?? "");
          setUseSessionVibeForExport(sessionVibeMatched(sv));
        } else {
          setSessionVibe(null);
        }
      } catch {
        if (!cancelled) setSessionVibe(null);
      }
    };
    void loadVibe();
    return () => {
      cancelled = true;
    };
  }, [API_BASE, reloadNonce]);

  const openVibeStylePreview = useCallback(
    (sv: SessionVibeState, pool: GalleryItem[]) => {
      if (!sessionVibeMatched(sv)) {
        setActionMsg(
          `助手写入了风格「${sv.label_zh || sv.prompt}」，但未匹配到胶片型号；请换关键词或在 Lab 手动选胶片。`,
        );
        return;
      }
      if (pool.length === 0) {
        pendingVibePreviewRef.current = true;
        setActionMsg(`助手已应用「${sv.label_zh}」，照片加载后将打开风格预览…`);
        return;
      }
      pendingVibePreviewRef.current = false;
      setAgentPreviewVariant("vibe");
      setAgentPreviewItems(pool.slice(0, 12));
      setSelectionPreviewOpen(false);
      setActionMsg(`助手已应用「${sv.label_zh}」，已打开风格预览`);
    },
    [],
  );

  // ChatDock skills (search preview / select / vibe / export) → Gallery UI.
  useEffect(() => {
    const onAgentAction = (ev: Event) => {
      const detail = (
        ev as CustomEvent<{ action?: string; metadata?: Record<string, unknown> }>
      ).detail;
      const action = String(detail?.action || "");
      const meta = detail?.metadata ?? {};
      if (action === "search") {
        const files = Array.isArray(meta.files)
          ? meta.files.map((f) => String(f || "").trim()).filter(Boolean)
          : [];
        if (files.length === 0) {
          setActionMsg("助手未找到匹配照片");
          return;
        }
        const byBase = new Map<string, GalleryItem>();
        for (const it of items) {
          const base = catalogBasenameForExport(it);
          if (base) byBase.set(base, it);
          if (it.file?.trim()) byBase.set(it.file.trim(), it);
        }
        const root = (galleryBasePath || "").replace(/\/$/, "");
        const resolved: GalleryItem[] = [];
        for (const f of files) {
          const hit = byBase.get(f);
          if (hit) {
            resolved.push(hit);
            continue;
          }
          const abs = root ? `${root}/${f}` : f;
          resolved.push({
            file: f,
            path: abs,
            path_quoted: encodeURIComponent(abs),
          });
        }
        setAgentPreviewVariant("agent");
        setAgentPreviewItems(resolved);
        setSelectionPreviewOpen(false);
        setActionMsg(`助手筛选 ${resolved.length} 张，已打开预览`);
        return;
      }
      if (action === "reload_vibe") {
        const raw = meta.session_vibe;
        const sv =
          raw && typeof raw === "object" ? (raw as SessionVibeState) : null;
        setReloadNonce((n) => n + 1);
        if (!sv?.film_variant) {
          setSessionVibe(null);
          setUseSessionVibeForExport(false);
          pendingVibePreviewRef.current = false;
          setActionMsg("助手已清除胶片风格");
          return;
        }
        setSessionVibe(sv);
        setVibePrompt(sv.prompt ?? "");
        setUseSessionVibeForExport(sessionVibeMatched(sv));
        const liked = items.filter((it, idx) =>
          selectedKeys.has(gallerySelectionKey(it, idx) || `item-${idx}`),
        );
        openVibeStylePreview(sv, liked.length > 0 ? liked : items);
        return;
      }
      if (action === "reload_curation" || action === "export_done") {
        setReloadNonce((n) => n + 1);
        if (action === "reload_curation") setActionMsg("助手已更新选片，正在刷新…");
        if (action === "export_done") setActionMsg("助手已触发导出（预览 + RAW）");
      }
    };
    window.addEventListener("luma:gallery-agent-action", onAgentAction as EventListener);
    return () => window.removeEventListener("luma:gallery-agent-action", onAgentAction as EventListener);
  }, [items, galleryBasePath, selectedKeys, openVibeStylePreview]);

  // Off-gallery ChatDock → /gallery? open graded vibe preview once items + vibe are ready.
  useEffect(() => {
    try {
      if (sessionStorage.getItem("luma.open_vibe_preview") !== "1") return;
    } catch {
      return;
    }
    if (!sessionVibeMatched(sessionVibe) || !sessionVibe || items.length === 0) {
      pendingVibePreviewRef.current = true;
      return;
    }
    try {
      sessionStorage.removeItem("luma.open_vibe_preview");
    } catch {
      /* ignore */
    }
    openVibeStylePreview(sessionVibe, items);
  }, [sessionVibe, items, openVibeStylePreview]);

  useEffect(() => {
    if (!pendingVibePreviewRef.current) return;
    if (!sessionVibeMatched(sessionVibe) || !sessionVibe || items.length === 0) return;
    openVibeStylePreview(sessionVibe, items);
  }, [sessionVibe, items, openVibeStylePreview]);

  const onApplySessionVibe = async () => {
    const text = vibePrompt.trim();
    if (!text) {
      setActionMsg("请输入风格描述，例如：浪漫复古、理光街拍、电影感");
      return;
    }
    setVibeBusy(true);
    setActionMsg("");
    try {
      const data = await saveSessionVibe(API_BASE, text);
      const sv = data.session_vibe;
      if (!sv?.film_variant) {
        throw new Error("服务端未返回有效胶片型号");
      }
      setSessionVibe(sv);
      const ok = sessionVibeMatched(sv);
      setUseSessionVibeForExport(ok);
      if (ok) {
        setActionMsg(`已应用会话风格：${sv.label_zh}（${sv.film_variant}）`);
      } else {
        setActionMsg(
          `未能识别「${text.slice(0, 24)}」对应的胶片风格；请换关键词或打开 Lab 手动选胶片。${sv.reason_zh ? ` ${sv.reason_zh}` : ""}`,
        );
      }
    } catch (e: unknown) {
      setActionMsg(`风格应用失败: ${e instanceof Error ? e.message : "unknown"}`);
    } finally {
      setVibeBusy(false);
    }
  };

  const onClearSessionVibe = async () => {
    setVibeBusy(true);
    try {
      await clearSessionVibeApi(API_BASE);
      setSessionVibe(null);
      setUseSessionVibeForExport(false);
      setVibePrompt("");
      setActionMsg("已清除会话 Vibe 风格");
    } catch (e: unknown) {
      setActionMsg(`清除失败: ${e instanceof Error ? e.message : "unknown"}`);
    } finally {
      setVibeBusy(false);
    }
  };

  useEffect(() => {
    if (!paginated || !hasMore) return;
    let cancelled = false;
    let idleId: number | null = null;
    const schedule = () => {
      const runner = async () => {
        if (cancelled) return;
        if (nextOffset == null) return;
        if (prefetchedRef.current?.offset === nextOffset) return;
        try {
          const payload = await fetchGalleryResultsPage(
            API_BASE,
            nextOffset,
            GALLERY_PAGE_LIMIT,
            undefined,
            galleryBurstDedupe,
            gallerySort,
          );
          if (!cancelled) prefetchedRef.current = { offset: nextOffset, payload };
          if (!cancelled) {
            const prefItems: GalleryItem[] = (payload as any)?.items ?? [];
            enqueueDecode(prefItems.slice(0, 24).map(buildImageUrl));
          }
        } catch {
          /* ignore */
        }
      };
      if (typeof window !== "undefined" && "requestIdleCallback" in window) {
        idleId = (window as any).requestIdleCallback(() => runner(), { timeout: 1500 });
      } else {
        idleId = globalThis.setTimeout(() => runner(), 700) as unknown as number;
      }
    };
    schedule();
    return () => {
      cancelled = true;
      if (idleId != null) {
        if (typeof window !== "undefined" && "cancelIdleCallback" in window) {
          (window as any).cancelIdleCallback(idleId);
        } else {
          clearTimeout(idleId);
        }
      }
    };
  }, [hasMore, nextOffset, loadingItems, loadingMore, paginated, enqueueDecode, galleryBurstDedupe, gallerySort]);

  const titleCount = datasetTotal != null && datasetTotal > 0 ? datasetTotal : items.length;
  const photoCountLabel = loadingItems && items.length === 0 ? "…" : String(titleCount);
  const photoCountSuffix =
    (galleryBurstDedupe || gallerySort === "diverse") &&
    datasetTotalRaw != null &&
    datasetTotalRaw > titleCount &&
    titleCount > 0
      ? ` · 原 ${datasetTotalRaw}`
      : "";

  const headerStatus = useMemo(() => {
    const w = queueBacklog?.workers?.length ?? 0;
    const t = queueBacklog?.totals;
    const active = t?.active ?? 0;
    const res = t?.reserved ?? 0;
    const sch = t?.scheduled ?? 0;
    const busyJobs = active + res + sch;
    const queueLabel = queueBacklog?.celery_unavailable ? "n/a" : busyJobs === 0 ? "idle" : `${busyJobs} jobs`;
    const redisOk = !queueBacklog?.redis_error;
    return { workerCount: w, queueLabel, redisOk };
  }, [queueBacklog]);

  const selectedItems = useMemo(() => {
    return items.filter((it, idx) =>
      selectedKeys.has(gallerySelectionKey(it, idx) || `item-${idx}`),
    );
  }, [items, selectedKeys]);

  const modalSelectionKey = useMemo(() => {
    if (!modal) return "";
    const idx = items.indexOf(modal);
    return gallerySelectionKey(modal, idx >= 0 ? idx : undefined);
  }, [modal, items]);

  const modalLikeReasons = useMemo((): CurationLikeReason[] => {
    if (!modalSelectionKey) return [];
    const ent = feedbackByKey[modalSelectionKey];
    if (ent?.verdict !== "liked") return [];
    return ent.like_reasons ?? [];
  }, [feedbackByKey, modalSelectionKey]);

  const onToggleSelect = (item: GalleryItem, checked: boolean) => {
    const idx = items.indexOf(item);
    const key = gallerySelectionKey(item, idx >= 0 ? idx : undefined);
    if (!key) return;
    setFeedbackByKey((prev) => setFeedbackVerdict(prev, key, checked ? "liked" : null));
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
    if (!checked) {
      setExportByFile((prev) => {
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      });
    } else {
      const prefKey = gallerySelectionKey(item);
      if (!prefKey) return;
      setExportByFile((prev) => {
        if (prev[prefKey]) return prev;
        const spec = defaultFilmExportItem(item);
        if (!spec) return prev;
        return { ...prev, [prefKey]: spec };
      });
    }
  };

  const clearSelection = () => {
    setSelectedKeys(new Set());
    setFeedbackByKey({});
    setExportByFile({});
    if (curationHydratedRef.current) {
      void saveGalleryCuration(API_BASE, buildCurationSavePayload({}, {})).catch(() => {});
    }
  };

  const onBatchExport = async () => {
    const itemsPayload: GalleryExportItem[] = selectedItems
      .map((it) => {
        const f = catalogBasenameForExport(it);
        if (!f) return null;
        const rotate = Number(it.rotate_degrees ?? 0);
        const prefKey = gallerySelectionKey(it);
        const ex = prefKey ? exportByFile[prefKey] : undefined;
        if (!ex) return { file: f, rotate };
        return { ...ex, file: f, rotate };
      })
      .filter(Boolean) as GalleryExportItem[];
    if (!itemsPayload.length) {
      setActionMsg(
        "无法导出：选中的条目缺少 catalog 文件名（无 file 且无法从 path 解析出文件名）。请确认数据或改用带 file 字段的接口结果。",
      );
      return;
    }
    setBusy("export");
    setActionMsg("");
    let body: string;
    try {
      body = serializeExportRequestBody(itemsPayload, "best", {
        useSessionVibe: useSessionVibeForExport && sessionVibeMatched(sessionVibe),
      });
    } catch (e: unknown) {
      setActionMsg(`导出失败: ${e instanceof Error ? e.message : "unknown"}`);
      setBusy(null);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/export-images`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      const rawText = await res.text();
      let data: {
        success?: boolean;
        error?: string;
        errors?: string[];
        detail?: string;
        export_path?: string;
        count_jpeg?: number;
        count?: number;
        count_raw?: number;
        count_graded_from_raw?: number;
        graded_from_raw_folder?: string;
        export_film_from_raw?: boolean;
      };
      try {
        data = rawText ? JSON.parse(rawText) : {};
      } catch {
        const snippet = rawText.replace(/\s+/g, " ").trim().slice(0, 200);
        const looksHtml = /^\s*</.test(rawText) || snippet.includes("<!DOCTYPE");
        throw new Error(
          looksHtml
            ? `导出请求失败（HTTP ${res.status}）：Next 代理或 gallery_server 异常。请确认 gallery_server 在 8080 运行；大批量导出可设置 web/.env.local 中 NEXT_PUBLIC_API_BASE=http://127.0.0.1:8080 直连 API。${snippet ? ` 响应片段：${snippet}` : ""}`
            : `服务器返回非 JSON（HTTP ${res.status}）。${snippet || "请确认 gallery_server 已启动且 GALLERY_API_ORIGIN 指向 8080。"}`,
        );
      }
      if (!res.ok) {
        const msg = formatApiErrorDetail(data, { httpStatus: res.status, rawBody: rawText });
        const hint =
          res.status === 422
            ? "（422 多为请求 JSON 字段与后端模型不一致，可打开浏览器开发者工具 → Network 查看响应体。）"
            : "";
        throw new Error(`[HTTP ${res.status}] ${msg}${hint}`);
      }
      if (!data?.success) {
        const errList = Array.isArray(data.errors) ? data.errors.filter(Boolean).slice(0, 5).join("；") : "";
        throw new Error(
          [data.error ?? "导出失败（未写入任何 JPEG/RAW）", errList].filter(Boolean).join(" — "),
        );
      }
      const j = data.count_jpeg ?? data.count ?? 0;
      const r = data.count_raw ?? 0;
      const g = data.count_graded_from_raw ?? 0;
      const dirs =
        g > 0 || data.export_film_from_raw
          ? "jpeg/、raw/、graded_from_raw/"
          : "jpeg/ 与 raw/";
      const gradedNote = g > 0 ? `、RAW 显影+胶片 ${g} 张` : "";
      setActionMsg(`已导出 Preview 胶片 ${j} 张、RAW 拷贝 ${r} 张${gradedNote} → ${data.export_path}（${dirs}）`);
    } catch (e: unknown) {
      setActionMsg(`导出失败: ${e instanceof Error ? e.message : "unknown"}`);
    } finally {
      setBusy(null);
    }
  };

  const onRunEnhance = async () => {
    setBusy("enhance");
    setActionMsg("");
    try {
      const res = await fetch(`${API_BASE}/api/tasks/analyze`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail ?? "任务提交失败");
      setActionMsg(`AI 强化任务已提交: ${data.task_id}`);
    } catch (e: unknown) {
      setActionMsg(`任务提交失败: ${e instanceof Error ? e.message : "unknown"}`);
    } finally {
      setBusy(null);
    }
  };

  const onRetryLoad = () => setReloadNonce((n) => n + 1);

  const showEmptyPanel = !loadingItems && items.length === 0;

  return (
    <div className="flex min-h-screen flex-col bg-[#0a0a0a] text-white">
      <StudioAppNav />
      <main className="flex flex-1 flex-col pb-8">
      {selectedItems.length > 0 ? (
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="fixed left-1/2 top-2 z-50 flex max-w-[min(18rem,calc(100vw-2rem))] -translate-x-1/2 items-center gap-1.5 rounded-full bg-black/35 px-2.5 py-1 text-[11px] text-white/50 backdrop-blur-[2px]"
        >
          <span className="text-white/35" aria-hidden>
            ✔
          </span>
          <span className="tabular-nums tracking-tight">
            已选 <span className="text-white/70">{selectedItems.length}</span> 张
          </span>
        </div>
      ) : null}

      <header className="shrink-0 border-b border-white/[0.04] px-[clamp(14px,3.5vw,44px)] pb-4 pt-5">
        <div className={`mx-auto flex w-full ${GALLERY_MASONRY_MAX_CLASS} flex-col gap-4 lg:flex-row lg:items-start lg:justify-between`}>
          <div className="min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-[0.22em] text-white/40">Gallery</p>
            <h1 className="mt-1.5 text-[clamp(1.35rem,4vw,2rem)] font-light leading-tight tracking-tight text-white">
              Luma Lab{" "}
              <span className="text-white/30" aria-hidden>
                ·
              </span>{" "}
              <span className="tabular-nums text-white/45">
                {photoCountLabel === "…" ? "…" : `${photoCountLabel} photos${photoCountSuffix}`}
              </span>
            </h1>
            {galleryBasePath ? (
              <p className="mt-2 truncate font-mono text-[11px] leading-snug text-white/28" title={galleryBasePath}>
                {galleryBasePath}
              </p>
            ) : null}
            {!loadingItems && loadSource !== "none" ? (
              <p className="mt-1.5 text-[10px] text-white/25">
                数据源:{" "}
                {loadSource === "results_api" ? "API（分页）" : "analysis_results.json（客户端回退）"}
              </p>
            ) : null}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="text-[10px] text-white/30">连拍</span>
              <div
                className="inline-flex rounded-[4px] border border-white/[0.08] p-0.5"
                role="group"
                aria-label="连拍折叠"
              >
                <button
                  type="button"
                  aria-pressed={galleryBurstDedupe}
                  onClick={() => setGalleryBurstDedupePref(true)}
                  className={[
                    "rounded-[3px] px-2.5 py-1 text-[10px] transition-colors",
                    galleryBurstDedupe
                      ? "bg-white/[0.12] text-white/85"
                      : "text-white/45 hover:text-white/65",
                  ].join(" ")}
                >
                  精简（每簇 1 张）
                </button>
                <button
                  type="button"
                  aria-pressed={!galleryBurstDedupe}
                  onClick={() => setGalleryBurstDedupePref(false)}
                  className={[
                    "rounded-[3px] px-2.5 py-1 text-[10px] transition-colors",
                    !galleryBurstDedupe
                      ? "bg-white/[0.12] text-white/85"
                      : "text-white/45 hover:text-white/65",
                  ].join(" ")}
                >
                  显示全部
                </button>
              </div>
              <span className="text-[10px] text-white/22">
                按画面相似度折叠连拍；相似场景只留分数最高的一张
              </span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="text-[10px] text-white/30">排序</span>
              <div
                className="inline-flex rounded-[4px] border border-white/[0.08] p-0.5"
                role="group"
                aria-label="相册排序"
              >
                <button
                  type="button"
                  aria-pressed={gallerySort === "overall"}
                  onClick={() => {
                    setGalleryDiversePref(false);
                    setGallerySortPersonalizedPref(false);
                  }}
                  className={[
                    "rounded-[3px] px-2.5 py-1 text-[10px] transition-colors",
                    gallerySort === "overall"
                      ? "bg-white/[0.12] text-white/85"
                      : "text-white/45 hover:text-white/65",
                  ].join(" ")}
                >
                  AI 综合分
                </button>
                <button
                  type="button"
                  aria-pressed={gallerySort === "diverse"}
                  onClick={() => setGalleryDiversePref(true)}
                  title="按画面相似度聚类，每组只展示一张代表帧，可展开同款"
                  className={[
                    "rounded-[3px] px-2.5 py-1 text-[10px] transition-colors",
                    gallerySort === "diverse"
                      ? "bg-white/[0.12] text-white/85"
                      : "text-white/45 hover:text-white/65",
                  ].join(" ")}
                >
                  多样性
                </button>
                <button
                  type="button"
                  aria-pressed={gallerySort === "personalized"}
                  onClick={() => setGallerySortPersonalizedPref(true)}
                  disabled={!tasteProfile}
                  title={!tasteProfile ? "需先勾选≥5张并保存以学习口味" : undefined}
                  className={[
                    "rounded-[3px] px-2.5 py-1 text-[10px] transition-colors disabled:opacity-35",
                    gallerySort === "personalized"
                      ? "bg-white/[0.12] text-white/85"
                      : "text-white/45 hover:text-white/65",
                  ].join(" ")}
                >
                  我的口味
                </button>
              </div>
              <button
                type="button"
                onClick={() => {
                  void rebuildTasteProfile(API_BASE)
                    .then(() => refreshTasteProfile())
                    .then(() => setTasteMsg("口味模型已更新"))
                    .catch((e: unknown) =>
                      setTasteMsg(e instanceof Error ? e.message : "更新失败（需≥5张已选且有 audit 维度）"),
                    );
                }}
                className="rounded-[3px] border border-white/[0.06] px-2 py-1 text-[10px] text-white/40 hover:text-white/60"
              >
                重新学习
              </button>
              {tasteTopHints(tasteProfile).length > 0 ? (
                <span className="text-[10px] text-white/28">{tasteTopHints(tasteProfile).join(" · ")}</span>
              ) : tasteMsg ? (
                <span className="text-[10px] text-white/22">{tasteMsg}</span>
              ) : null}
            </div>
            <div className="mt-4 w-full max-w-xl">
              <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-white/35">Vibe 修图</p>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  type="text"
                  value={vibePrompt}
                  onChange={(e) => setVibePrompt(e.target.value)}
                  placeholder="例如：浪漫复古、理光街拍、电影感"
                  className="min-w-0 flex-1 rounded-[4px] border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[12px] text-white/80 placeholder:text-white/28 focus:border-white/[0.14] focus:outline-none"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onApplySessionVibe();
                  }}
                />
                <button
                  type="button"
                  disabled={vibeBusy}
                  onClick={() => void onApplySessionVibe()}
                  className="shrink-0 rounded-[4px] border border-white/[0.1] bg-white/[0.06] px-3 py-2 text-[11px] text-white/70 transition-colors hover:bg-white/[0.1] disabled:opacity-40"
                >
                  {vibeBusy ? "应用中…" : "应用风格"}
                </button>
                {sessionVibeMatched(sessionVibe) && sessionVibe ? (
                  <button
                    type="button"
                    disabled={vibeBusy || items.length === 0}
                    onClick={() => {
                      const liked = items.filter((it, idx) =>
                        selectedKeys.has(gallerySelectionKey(it, idx) || `item-${idx}`),
                      );
                      openVibeStylePreview(sessionVibe, liked.length > 0 ? liked : items);
                    }}
                    className="shrink-0 rounded-[4px] border border-emerald-400/25 bg-emerald-400/[0.08] px-3 py-2 text-[11px] text-emerald-100/85 transition-colors hover:bg-emerald-400/[0.14] disabled:opacity-40"
                  >
                    预览风格
                  </button>
                ) : null}
                {sessionVibe ? (
                  <button
                    type="button"
                    disabled={vibeBusy}
                    onClick={() => void onClearSessionVibe()}
                    className="shrink-0 rounded-[4px] border border-white/[0.06] px-3 py-2 text-[11px] text-white/40 hover:text-white/55 disabled:opacity-40"
                  >
                    清除
                  </button>
                ) : null}
              </div>
              {sessionVibe ? (
                <div className="mt-2 space-y-1.5">
                  {sessionVibeMatched(sessionVibe) ? (
                    <p className="text-[11px] text-white/40">
                      当前会话：<span className="text-white/65">{sessionVibe.label_zh}</span>
                      <span className="text-white/25"> · {sessionVibe.film_variant}</span>
                      {sessionVibe.reason_zh ? (
                        <span className="block text-[10px] text-white/28">{sessionVibe.reason_zh}</span>
                      ) : null}
                    </p>
                  ) : (
                    <p className="rounded-[4px] border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 text-[11px] leading-relaxed text-amber-100/85">
                      未能匹配胶片风格，批量导出不会自动套用会话 Vibe。请修改描述后重新「应用风格」，或在 Lab
                      里手动选胶片。
                      {sessionVibe.reason_zh ? (
                        <span className="mt-1 block text-[10px] text-amber-100/60">{sessionVibe.reason_zh}</span>
                      ) : null}
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-2 text-[10px] text-white/25">
                  输入描述后应用；Lab 打开时会默认选中对应胶片预览。
                </p>
              )}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2 lg:pt-1">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-[11px] font-normal tabular-nums text-white/55">
              Workers: <span className="text-white/75">{headerStatus.workerCount}</span>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-[11px] font-normal tabular-nums text-white/55">
              Queue: <span className="text-white/75">{headerStatus.queueLabel}</span>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-[11px] font-normal text-white/55">
              <span
                className="h-1.5 w-1.5 rounded-full bg-emerald-400/90 shadow-[0_0_10px_rgba(52,211,153,0.45)]"
                aria-hidden
              />
              Redis:{" "}
              <span className={headerStatus.redisOk ? "text-emerald-300/90" : "text-rose-300/90"}>
                {headerStatus.redisOk ? "OK" : "err"}
              </span>
            </span>
          </div>
        </div>
      </header>

      {galleryErr ? (
        <div className="mx-4 mb-3 rounded-[2px] border border-white/[0.04] px-3 py-2 text-[12px] text-white/40">
          {galleryErr}
        </div>
      ) : null}

      <div className="flex w-full min-w-0 flex-1 flex-col pt-2">
        {loadingItems && items.length === 0 ? (
          <div className="flex min-h-[70svh] items-center justify-center px-4 text-center text-[12px] text-white/30">
            加载相册…
          </div>
        ) : showEmptyPanel ? (
          <GalleryEmptyState
            activeDir={galleryBasePath}
            loadSource={loadSource}
            apiError={bootstrapErr}
            onRetry={onRetryLoad}
          />
        ) : (
          <div className="w-full min-w-0 flex-1">
            <div className="min-h-[min(88svh,100svh)] w-full pb-6">
              <GalleryMasonry
                items={items}
                apiBase={API_BASE}
                onOpenLab={setModal}
                selectedKeys={selectedKeys}
                onToggleSelect={onToggleSelect}
              />
            </div>
          </div>
        )}

        {!loadingItems && !showEmptyPanel && paginated ? (
          <div
            ref={sentinelRef}
            className="mx-auto mb-6 min-h-[1rem] w-full max-w-md text-center text-[10px] tracking-wide text-white/22"
          >
            {loadingMore ? (
              <span aria-live="polite">加载更多…</span>
            ) : !hasMore ? (
              <span className="text-white/18">已全部加载</span>
            ) : null}
          </div>
        ) : null}
      </div>

      {selectedItems.length > 0 ? (
        <div className="fixed bottom-3 left-1/2 z-40 w-[min(920px,calc(100vw-48px))] -translate-x-1/2 rounded-[2px] border border-white/[0.04] bg-black/40 px-2.5 py-2 backdrop-blur-[4px]">
          <div className="flex flex-wrap items-center gap-1.5">
            <div className="mr-0.5 text-[11px] text-white/40">
              已选中 <span className="tabular-nums text-white/65">{selectedItems.length}</span> 张
              {curationSaveState === "saved" ? (
                <span className="ml-1.5 text-white/22">· 已自动保存</span>
              ) : curationSaveState === "saving" ? (
                <span className="ml-1.5 text-white/18">· 保存中</span>
              ) : null}
            </div>
            {sessionVibe && sessionVibeMatched(sessionVibe) ? (
              <label className="flex cursor-pointer items-center gap-1.5 rounded-[2px] border border-white/[0.05] px-2 py-1 text-[10px] text-white/45">
                <input
                  type="checkbox"
                  checked={useSessionVibeForExport}
                  onChange={(e) => setUseSessionVibeForExport(e.target.checked)}
                  className="accent-emerald-500/80"
                />
                会话 Vibe
              </label>
            ) : null}
            <button
              type="button"
              onClick={() => setSelectionPreviewOpen(true)}
              disabled={busy !== null}
              className="rounded-[2px] border border-white/[0.05] bg-transparent px-2 py-1 text-[11px] text-white/55 transition-colors duration-150 ease-out hover:bg-white/[0.03] hover:text-white/75 disabled:opacity-35"
            >
              预览选中
            </button>
            <button
              type="button"
              onClick={onBatchExport}
              disabled={busy !== null}
              className="rounded-[2px] border border-white/[0.05] bg-transparent px-2 py-1 text-[11px] text-white/55 transition-colors duration-150 ease-out hover:bg-white/[0.03] hover:text-white/75 disabled:opacity-35"
            >
              {busy === "export" ? "导出中…" : "批量导出"}
            </button>
            <button
              type="button"
              onClick={onRunEnhance}
              disabled={busy !== null}
              className="rounded-[2px] border border-white/[0.05] bg-transparent px-2 py-1 text-[11px] text-white/55 transition-colors duration-150 ease-out hover:bg-white/[0.03] hover:text-white/75 disabled:opacity-35"
            >
              {busy === "enhance" ? "提交中…" : "AI 强化"}
            </button>
            <button
              type="button"
              onClick={clearSelection}
              className="rounded-[2px] border border-white/[0.04] px-2 py-1 text-[11px] text-white/38 transition-colors duration-150 ease-out hover:border-white/[0.07] hover:text-white/52"
            >
              清空
            </button>
            {actionMsg ? <div className="ml-auto max-w-full text-[10px] text-white/32">{actionMsg}</div> : null}
          </div>
        </div>
      ) : null}

      {selectionPreviewOpen && selectedItems.length > 0 ? (
        <SelectedPreviewModal
          items={selectedItems}
          exportByFile={exportByFile}
          apiBase={API_BASE}
          onClose={() => setSelectionPreviewOpen(false)}
          sessionFilmVariant={
            sessionVibeMatched(sessionVibe) ? sessionVibe?.film_variant ?? null : null
          }
          useSessionVibe={useSessionVibeForExport && sessionVibeMatched(sessionVibe)}
        />
      ) : null}

      {agentPreviewItems && agentPreviewItems.length > 0 ? (
        <SelectedPreviewModal
          items={agentPreviewItems}
          exportByFile={exportByFile}
          apiBase={API_BASE}
          variant={agentPreviewVariant}
          onClose={() => setAgentPreviewItems(null)}
          sessionFilmVariant={
            sessionVibeMatched(sessionVibe) ? sessionVibe?.film_variant ?? null : null
          }
          useSessionVibe={
            agentPreviewVariant === "vibe"
              ? sessionVibeMatched(sessionVibe)
              : useSessionVibeForExport && sessionVibeMatched(sessionVibe)
          }
        />
      ) : null}

      {modal ? (
        <LabCompareModal
          item={modal}
          apiBase={API_BASE}
          onClose={() => setModal(null)}
          sessionFilmVariant={
            sessionVibeMatched(sessionVibe) ? sessionVibe?.film_variant ?? null : null
          }
          chosenExport={exportByFile[modalSelectionKey] ?? null}
          selectionKey={modalSelectionKey}
          isSelected={modalSelectionKey ? selectedKeys.has(modalSelectionKey) : false}
          likeReasons={modalLikeReasons}
          onToggleSelection={(checked) => onToggleSelect(modal, checked)}
          onToggleLikeReason={(reason) => {
            if (!modalSelectionKey) return;
            setFeedbackByKey((prev) => toggleFeedbackLikeReason(prev, modalSelectionKey, reason));
          }}
          onPickExportStyle={(spec, label) => {
            const prefKey = modalSelectionKey || gallerySelectionKey(modal);
            if (!prefKey) return;
            const file = catalogBasenameForExport(modal) ?? spec.file?.trim();
            if (!file) return;
            setExportByFile((prev) => ({ ...prev, [prefKey]: { ...spec, file } }));
            setFeedbackByKey((prev) => setFeedbackVerdict(prev, prefKey, "liked"));
            setSelectedKeys((prev) => {
              const next = new Set(prev);
              next.add(prefKey);
              return next;
            });
            setActionMsg(`已选导出效果「${label}」· ${file}（可关闭窗口后在底部栏批量导出）`);
          }}
        />
      ) : null}
      </main>
    </div>
  );
}
