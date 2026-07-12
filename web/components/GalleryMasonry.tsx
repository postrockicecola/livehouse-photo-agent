"use client";

import {
  startTransition,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type SyntheticEvent,
} from "react";
import { gallerySelectionKey } from "@/lib/defaultFilmExport";
import { buildGalleryPlainImageUrl } from "@/lib/galleryDisplayUrl";
import { GALLERY_MASONRY_MAX_CLASS, useGalleryMasonryColumnCount } from "@/lib/galleryLayout";
import type { GalleryItem } from "./types";

type Props = {
  items: GalleryItem[];
  apiBase: string;
  onOpenLab: (item: GalleryItem) => void;
  selectedKeys: Set<string>;
  onToggleSelect: (item: GalleryItem, checked: boolean) => void;
};

type PlacedItem = { item: GalleryItem; index: number };

type IntrinsicSize = { w: number; h: number };

function stableItemKey(item: GalleryItem, index: number): string {
  const f = item.file ?? "";
  const p = item.path ?? "";
  const q = item.path_quoted ?? "";
  if (f || p || q) return `${f}\0${p}\0${q}`;
  return `__unnamed:${index}`;
}

function apiLayoutHint(item: GalleryItem): { w: number; h: number } {
  const w = Number(item.width) || 0;
  const h = Number(item.height) || 0;
  return { w, h };
}

function displayOrientationFromSize(w: number, h: number): "landscape" | "portrait" | "square" | "unknown" {
  if (w <= 0 || h <= 0) return "unknown";
  if (Math.abs(w - h) < 1e-3) return "square";
  return w > h ? "landscape" : "portrait";
}

function displayOrientation(item: GalleryItem, measured: IntrinsicSize | null): "landscape" | "portrait" | "square" | "unknown" {
  if (measured) return displayOrientationFromSize(measured.w, measured.h);
  const { w, h } = apiLayoutHint(item);
  if (w > 0 && h > 0) return displayOrientationFromSize(w, h);
  const o = String(item.orientation ?? "").toLowerCase();
  if (o === "landscape" || o === "portrait" || o === "square") return o;
  return "unknown";
}

function masonryWeight(
  item: GalleryItem,
  index: number,
  measuredByKey: ReadonlyMap<string, IntrinsicSize>,
): number {
  const key = stableItemKey(item, index);
  const m = measuredByKey.get(key);
  if (m && m.w > 0 && m.h > 0) return m.h / m.w;
  const { w, h } = apiLayoutHint(item);
  if (w > 0 && h > 0) return h / w;
  const o = displayOrientation(item, null);
  if (o === "portrait") return 1.35;
  if (o === "landscape") return 0.65;
  return 1;
}

function sortKeyForTiebreak(item: GalleryItem): string {
  return `${item.file ?? ""}\0${item.path ?? ""}`;
}

function sortItemsByScoreDesc(items: GalleryItem[]): GalleryItem[] {
  return [...items].sort((a, b) => {
    const sa = Number(a.overall_score ?? 0);
    const sb = Number(b.overall_score ?? 0);
    if (sb !== sa) return sb - sa;
    return sortKeyForTiebreak(a).localeCompare(sortKeyForTiebreak(b), "en");
  });
}

function splitIntoNMasonryColumns(
  items: GalleryItem[],
  n: number,
  measuredByKey: ReadonlyMap<string, IntrinsicSize>,
): PlacedItem[][] {
  if (n <= 1) return [items.map((item, index) => ({ item, index }))];

  const cols: PlacedItem[][] = Array.from({ length: n }, () => []);
  const heights = new Float64Array(n);

  items.forEach((item, index) => {
    const weight = masonryWeight(item, index, measuredByKey);
    let best = 0;
    let minH = heights[0];
    for (let i = 1; i < n; i++) {
      if (heights[i] < minH - 1e-6) {
        minH = heights[i];
        best = i;
      }
    }
    cols[best].push({ item, index });
    heights[best] += weight;
  });

  return cols;
}

function captionFromFile(name: string | undefined) {
  if (!name?.trim()) return "Untitled";
  return name.length > 48 ? `${name.slice(0, 45)}…` : name;
}

export function GalleryMasonry({
  items,
  apiBase,
  onOpenLab,
  selectedKeys,
  onToggleSelect,
}: Props) {
  const columnCount = useGalleryMasonryColumnCount();
  const sortedItems = useMemo(() => sortItemsByScoreDesc(items), [items]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set());

  const toggleGroup = useCallback((key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const [measuredByKey, setMeasuredByKey] = useState<Map<string, IntrinsicSize>>(() => new Map());

  useEffect(() => {
    const allowed = new Set(sortedItems.map((it, i) => stableItemKey(it, i)));
    setMeasuredByKey((prev) => {
      let changed = false;
      const next = new Map<string, IntrinsicSize>();
      for (const [k, v] of prev) {
        if (allowed.has(k)) next.set(k, v);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [sortedItems]);

  const recordIntrinsic = useCallback((item: GalleryItem, index: number, w: number, h: number) => {
    if (w <= 0 || h <= 0) return;
    const key = stableItemKey(item, index);
    startTransition(() => {
      setMeasuredByKey((prev) => {
        const cur = prev.get(key);
        if (cur && cur.w === w && cur.h === h) return prev;
        const next = new Map(prev);
        next.set(key, { w, h });
        return next;
      });
    });
  }, []);

  const columns = useMemo(
    () => splitIntoNMasonryColumns(sortedItems, columnCount, measuredByKey),
    [sortedItems, columnCount, measuredByKey],
  );

  const renderTile = (placed: PlacedItem) => {
    const { item, index } = placed;
    const itemKey = gallerySelectionKey(item, index) || `item-${index}`;
    const reactKey = stableItemKey(item, index);
    const checked = selectedKeys.has(itemKey);
    const score = Number(item.overall_score ?? 0);
    const measured = measuredByKey.get(reactKey) ?? null;
    const orient = displayOrientation(item, measured);
    const members = item.group_members ?? [];
    const groupSize = Number(item.group_size ?? 0);
    const hasGroup = groupSize > 1 && members.length > 0;
    const expanded = hasGroup && expandedGroups.has(reactKey);

    return (
      <article
        key={reactKey}
        data-orientation={orient}
        className={[
          "gallery-item group/tile relative block w-full min-w-0 overflow-hidden rounded-[2px] leading-none",
          checked
            ? "shadow-[inset_0_0_0_2px_rgba(52,211,153,0.42)]"
            : "",
        ].join(" ")}
      >
        <button
          type="button"
          aria-label={`打开预览：${item.file ?? "photo"}，评分 ${score.toFixed(1)}`}
          className="gallery-tile-button relative block w-full border-0 bg-transparent p-0 text-left [&:focus-visible]:outline-none [&:focus-visible]:ring-1 [&:focus-visible]:ring-inset [&:focus-visible]:ring-white/25"
          onClick={() => onOpenLab(item)}
        >
          <GalleryTileImage
            item={item}
            apiBase={apiBase}
            onMeasured={(w, h) => recordIntrinsic(item, index, w, h)}
          />
          <div className="gallery-caption-layer pointer-events-none absolute inset-x-0 bottom-0 z-10 bg-gradient-to-t from-black/55 via-transparent to-transparent px-2.5 pb-2 pt-8 sm:px-3 sm:pb-2.5 sm:pt-10">
            <div className="flex items-end justify-between gap-4 pb-0.5">
              <p className="min-w-0 flex-1 truncate text-[11px] font-light text-white/88">
                {captionFromFile(item.file)}
              </p>
              <span
                className="shrink-0 tabular-nums text-[10px] font-light tracking-wide text-white/55"
                title={`Score ${score.toFixed(1)}`}
              >
                {score.toFixed(1)}
              </span>
            </div>
          </div>
        </button>

        <button
          type="button"
          aria-pressed={checked}
          aria-label={checked ? "取消选择" : "选择"}
          title={checked ? "取消选择" : "选择"}
          className={[
            "absolute right-2 top-2 z-20 rounded-[4px] px-1.5 py-0.5 text-[9px] font-normal tracking-wide backdrop-blur-[2px] motion-safe:transition-[background-color,color,box-shadow] motion-safe:duration-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/30",
            checked
              ? "bg-emerald-500/35 text-emerald-50/95 shadow-[inset_0_0_0_0.5px_rgba(110,231,183,0.35)]"
              : "bg-black/45 text-white/75 hover:bg-black/55 hover:text-white/90",
          ].join(" ")}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onToggleSelect(item, !checked);
          }}
        >
          {checked ? "已选" : "选择"}
        </button>

        {hasGroup ? (
          <button
            type="button"
            aria-pressed={expanded}
            aria-label={expanded ? "收起同款" : `展开同款 ${groupSize} 张`}
            title={expanded ? "收起同款" : `同款 ${groupSize} 张，展开查看`}
            className={[
              "absolute left-2 top-2 z-20 rounded-[4px] px-1.5 py-0.5 text-[9px] font-normal tracking-wide backdrop-blur-[2px] motion-safe:transition-[background-color,color] motion-safe:duration-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/30",
              expanded
                ? "bg-sky-500/35 text-sky-50/95 shadow-[inset_0_0_0_0.5px_rgba(125,211,252,0.35)]"
                : "bg-black/45 text-white/75 hover:bg-black/55 hover:text-white/90",
            ].join(" ")}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              toggleGroup(reactKey);
            }}
          >
            {expanded ? "收起" : `同款 ×${groupSize}`}
          </button>
        ) : null}

        {expanded ? (
          <div className="flex flex-wrap gap-[6px] bg-black/40 p-[6px]">
            {members.map((m, mi) => {
              const msrc = buildGalleryPlainImageUrl(apiBase, m as GalleryItem);
              const mscore = Number(m.overall_score ?? 0);
              return (
                <button
                  key={`${reactKey}\0m${mi}\0${m.file ?? m.path ?? mi}`}
                  type="button"
                  aria-label={`打开同款：${m.file ?? "photo"}，评分 ${mscore.toFixed(1)}`}
                  title={`${m.file ?? ""} · ${mscore.toFixed(1)}`}
                  className="relative block h-16 w-16 shrink-0 overflow-hidden rounded-[2px] border-0 bg-white/[0.03] p-0 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/30"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onOpenLab(m as GalleryItem);
                  }}
                >
                  {msrc ? (
                    <img
                      src={msrc}
                      alt=""
                      role="presentation"
                      className="h-full w-full object-cover"
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <span className="flex h-full w-full items-center justify-center text-[8px] text-white/25">—</span>
                  )}
                </button>
              );
            })}
          </div>
        ) : null}
      </article>
    );
  };

  return (
    <section
      aria-label="作品列表"
      className={`gallery-shell mx-auto flex w-full ${GALLERY_MASONRY_MAX_CLASS} items-start gap-[6px] px-[clamp(14px,3.5vw,44px)] sm:gap-2`}
    >
      {columns.map((col, ci) => (
        <div key={ci} className="flex min-w-0 flex-1 flex-col gap-[6px] sm:gap-2">
          {col.map(renderTile)}
        </div>
      ))}
    </section>
  );
}

function GalleryTileImage({
  item,
  apiBase,
  onMeasured,
}: {
  item: GalleryItem;
  apiBase: string;
  onMeasured: (w: number, h: number) => void;
}) {
  const src = useMemo(() => buildGalleryPlainImageUrl(apiBase, item), [apiBase, item]);

  const onLoad = useCallback(
    (e: SyntheticEvent<HTMLImageElement>) => {
      const el = e.currentTarget;
      if (el.naturalWidth > 0 && el.naturalHeight > 0) {
        onMeasured(el.naturalWidth, el.naturalHeight);
      }
    },
    [onMeasured],
  );

  if (!src) {
    return (
      <div className="flex min-h-[100px] w-full items-center justify-center bg-white/[0.03] px-3 py-6 text-center text-[10px] leading-snug text-white/25">
        缺少可加载路径
        <br />
        <span className="font-mono text-white/18">{item.file ?? "—"}</span>
      </div>
    );
  }

  return (
    <div className="w-full">
      <img
        src={src}
        alt=""
        role="presentation"
        className="block h-auto w-full align-bottom transition-[filter,transform] duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] motion-safe:group-hover/tile:brightness-[1.04] motion-safe:group-hover/tile:scale-[1.006]"
        loading="lazy"
        decoding="async"
        onLoad={onLoad}
      />
    </div>
  );
}
