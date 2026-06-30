"use client";

import { useCallback, useEffect, useMemo, useState, type SyntheticEvent } from "react";
import type { GalleryItem } from "@/components/types";
import { previewLayoutOrient } from "@/lib/previewMosaicLayout";

type MosaicRow = {
  key: string;
  item: GalleryItem;
  label: string;
  url: string | null;
  index: number;
};

type Props = {
  rows: MosaicRow[];
  indexPad: number;
};

type IntrinsicSize = { w: number; h: number };

function layoutSizeHint(item: GalleryItem): IntrinsicSize | null {
  let w = Number(item.width) || 0;
  let h = Number(item.height) || 0;
  const rot = Math.abs(Math.trunc(Number(item.rotate_degrees ?? 0)) % 360);
  if ((rot === 90 || rot === 270) && w > 0 && h > 0) {
    const t = w;
    w = h;
    h = t;
  }
  if (w > 0 && h > 0) return { w, h };
  return null;
}

function masonryWeight(item: GalleryItem, measured: IntrinsicSize | null): number {
  const m = measured ?? layoutSizeHint(item);
  if (m && m.w > 0 && m.h > 0) return m.h / m.w;
  const o = previewLayoutOrient(item);
  if (o === "portrait") return 1.35;
  if (o === "landscape") return 0.65;
  return 1;
}

function splitIntoMasonryColumns(
  rows: MosaicRow[],
  columnCount: number,
  measuredByKey: ReadonlyMap<string, IntrinsicSize>,
): MosaicRow[][] {
  const n = Math.max(1, columnCount);
  const cols: MosaicRow[][] = Array.from({ length: n }, () => []);
  const heights = new Float64Array(n);

  for (const row of rows) {
    const measured = measuredByKey.get(row.key) ?? null;
    const weight = masonryWeight(row.item, measured);
    let best = 0;
    let minH = heights[0];
    for (let i = 1; i < n; i++) {
      if (heights[i] < minH - 1e-6) {
        minH = heights[i];
        best = i;
      }
    }
    cols[best].push(row);
    heights[best] += weight;
  }

  return cols;
}

function usePreviewColumnCount(): number {
  const [n, setN] = useState(2);

  useEffect(() => {
    const q2 = window.matchMedia("(min-width: 520px)");
    const sync = () => setN(q2.matches ? 2 : 1);
    sync();
    q2.addEventListener("change", sync);
    return () => q2.removeEventListener("change", sync);
  }, []);

  return n;
}

export function PreviewMosaicGrid({ rows, indexPad }: Props) {
  const columnCount = usePreviewColumnCount();
  const [measuredByKey, setMeasuredByKey] = useState<Map<string, IntrinsicSize>>(() => new Map());

  const onTileMeasure = useCallback((key: string, size: IntrinsicSize) => {
    setMeasuredByKey((prev) => {
      const cur = prev.get(key);
      if (cur && cur.w === size.w && cur.h === size.h) return prev;
      const next = new Map(prev);
      next.set(key, size);
      return next;
    });
  }, []);

  const columns = useMemo(
    () => splitIntoMasonryColumns(rows, columnCount, measuredByKey),
    [rows, columnCount, measuredByKey],
  );

  return (
    <div
      className="flex items-start gap-[6px] sm:gap-2"
      role="list"
      aria-label="选中预览瀑布流"
    >
      {columns.map((col, ci) => (
        <div key={`col-${ci}`} className="flex min-w-0 flex-1 flex-col gap-[6px] sm:gap-2">
          {col.map((row) => (
            <MosaicTile
              key={row.key}
              row={row}
              indexPad={indexPad}
              onMeasure={onTileMeasure}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function MosaicTile({
  row,
  indexPad,
  onMeasure,
}: {
  row: MosaicRow;
  indexPad: number;
  onMeasure: (key: string, size: IntrinsicSize) => void;
}) {
  const { item } = row;
  const num = String(row.index).padStart(indexPad, "0");
  const orient = previewLayoutOrient(item);

  const onImgLoad = useCallback(
    (e: SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
        onMeasure(row.key, { w: img.naturalWidth, h: img.naturalHeight });
      }
    },
    [onMeasure, row.key],
  );

  return (
    <article
      className="group/tile relative block w-full min-w-0 overflow-hidden rounded-[2px] leading-none"
      data-orientation={orient}
      role="listitem"
    >
      {row.url ? (
        <img
          src={row.url}
          alt={row.label}
          onLoad={onImgLoad}
          className="block h-auto w-full align-bottom transition-[filter,transform] duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] motion-safe:group-hover/tile:brightness-[1.03] motion-safe:group-hover/tile:scale-[1.008]"
          loading="lazy"
          decoding="async"
          draggable={false}
        />
      ) : (
        <div className="flex min-h-[100px] w-full items-center justify-center bg-white/[0.03] text-center text-[11px] font-light text-white/35">
          无法生成预览
        </div>
      )}
      <span className="pointer-events-none absolute left-2 top-2 z-10 rounded-[4px] bg-black/45 px-1.5 py-0.5 text-[9px] font-light tabular-nums tracking-[0.12em] text-white/70 backdrop-blur-[2px]">
        {num}
      </span>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 bg-gradient-to-t from-black/70 via-black/25 to-transparent px-2.5 pb-2 pt-8 opacity-0 transition-opacity duration-300 group-hover/tile:opacity-100 group-focus-within/tile:opacity-100">
        <p className="truncate text-[11px] font-light text-white/90">{item.file ?? "Untitled"}</p>
        <p className="mt-0.5 truncate text-[9px] font-light text-white/50">{row.label}</p>
      </div>
    </article>
  );
}
