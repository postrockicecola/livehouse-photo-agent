"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  A4_HEIGHT_MM,
  A4_WIDTH_MM,
  CELL_SIZE_MM,
  MAX_PHOTOS,
  cellHeightPercent,
  cellWidthPercent,
  layoutPhotoSheet,
  maxGridForA4,
  mmToPercentX,
  mmToPercentY,
  paginatePhotoCounts,
} from "@/lib/photoSheetLayout";

type UploadedPhoto = {
  id: string;
  url: string;
  name: string;
};

type PhotoPage = {
  pageIndex: number;
  photos: UploadedPhoto[];
  layout: ReturnType<typeof layoutPhotoSheet>;
};

function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function acceptImage(file: File): boolean {
  return file.type.startsWith("image/");
}

export function PhotoSheetEditor() {
  const [photos, setPhotos] = useState<UploadedPhoto[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const a4Limits = useMemo(() => maxGridForA4(), []);

  const pages = useMemo((): PhotoPage[] => {
    if (!photos.length) return [];
    const counts = paginatePhotoCounts(photos.length);
    const out: PhotoPage[] = [];
    let offset = 0;
    counts.forEach((count, pageIndex) => {
      out.push({
        pageIndex,
        photos: photos.slice(offset, offset + count),
        layout: layoutPhotoSheet(count),
      });
      offset += count;
    });
    return out;
  }, [photos]);

  const revokeAll = useCallback((items: UploadedPhoto[]) => {
    for (const p of items) URL.revokeObjectURL(p.url);
  }, []);

  // Revoke only on unmount — per-photo revoke happens in removePhoto / clearAll.
  const photosRef = useRef(photos);
  photosRef.current = photos;
  useEffect(() => {
    return () => revokeAll(photosRef.current);
  }, [revokeAll]);

  const addFiles = (files: FileList | File[]) => {
    const list = Array.from(files).filter(acceptImage);
    if (!list.length) return;
    setPhotos((prev) => {
      const room = MAX_PHOTOS - prev.length;
      if (room <= 0) return prev;
      const next = list.slice(0, room).map((f) => ({
        id: uid(),
        url: URL.createObjectURL(f),
        name: f.name,
      }));
      return [...prev, ...next];
    });
  };

  const removePhoto = (id: string) => {
    setPhotos((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target) URL.revokeObjectURL(target.url);
      return prev.filter((p) => p.id !== id);
    });
  };

  const clearAll = () => {
    setPhotos((prev) => {
      revokeAll(prev);
      return [];
    });
  };

  const onPrint = () => {
    window.print();
  };

  const needsMultiPage = photos.length > a4Limits.maxPerPage;

  return (
    <div className="photo-sheet-editor">
      <div className="no-print mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-light text-white/88 sm:text-xl">6.5×6.5 cm · A4 排版</h2>
          <p className="mt-1 text-xs leading-relaxed text-white/40">
            上传 1–9 张图片；每张 6.5 cm × 6.5 cm，间距 1 cm。单页 A4 最多{" "}
            {a4Limits.maxPerPage} 张（{a4Limits.maxCols} 列 × {a4Limits.maxRows} 行），超出自动分页打印。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={photos.length >= MAX_PHOTOS}
            className="rounded-lg border border-sky-500/25 bg-sky-950/25 px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-sky-200/80 transition hover:border-sky-400/40 disabled:opacity-40"
          >
            添加图片
          </button>
          {photos.length > 0 ? (
            <>
              <button
                type="button"
                onClick={onPrint}
                className="rounded-lg border border-white/12 bg-white/[0.04] px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-white/70 transition hover:bg-white/[0.07]"
              >
                打印 / 导出 PDF
              </button>
              <button
                type="button"
                onClick={clearAll}
                className="rounded-lg border border-white/8 px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-white/40 transition hover:text-white/60"
              >
                清空
              </button>
            </>
          ) : null}
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) addFiles(e.target.files);
          e.target.value = "";
        }}
      />

      <div
        className={`no-print mb-8 rounded-xl border border-dashed p-8 text-center transition-colors ${
          dragOver ? "border-sky-400/50 bg-sky-950/20" : "border-white/10 bg-white/[0.02]"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
        }}
      >
        <p className="font-mono text-[11px] text-white/45">
          拖拽图片到此处，或{" "}
          <button
            type="button"
            className="text-sky-300/80 underline-offset-2 hover:underline"
            onClick={() => inputRef.current?.click()}
          >
            点击选择
          </button>
        </p>
        <p className="mt-2 font-mono text-[10px] text-white/25">
          {photos.length}/{MAX_PHOTOS} 张 · 单张 6.5×6.5 cm · 间距 1 cm
          {needsMultiPage ? ` · 将分 ${pages.length} 页 A4` : null}
        </p>
      </div>

      {photos.length > 0 ? (
        <div className="no-print mb-6 flex flex-wrap gap-2">
          {photos.map((p, i) => (
            <div
              key={p.id}
              className="group relative h-16 w-16 overflow-hidden rounded-md border border-white/10 bg-black/40"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={p.url} alt={p.name} className="h-full w-full object-cover" />
              <span className="absolute left-1 top-1 rounded bg-black/60 px-1 font-mono text-[9px] text-white/70">
                {i + 1}
              </span>
              <button
                type="button"
                onClick={() => removePhoto(p.id)}
                className="absolute right-0.5 top-0.5 flex h-5 w-5 items-center justify-center rounded bg-black/70 font-mono text-[11px] text-white/90 transition hover:bg-black/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                aria-label={`移除 ${p.name}`}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="photo-sheet-preview-wrap no-print">
        <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.18em] text-white/30">
          A4 预览{pages.length > 1 ? ` · 共 ${pages.length} 页` : ""}
        </p>
        {pages.length === 0 ? (
          <PhotoSheetCanvas photos={[]} layout={layoutPhotoSheet(0)} />
        ) : (
          <div className="space-y-8">
            {pages.map((page) => (
              <div key={page.pageIndex}>
                {pages.length > 1 ? (
                  <p className="mb-2 text-center font-mono text-[10px] text-white/30">
                    第 {page.pageIndex + 1} 页 · {page.photos.length} 张 · 布局 {page.layout.cols}×
                    {page.layout.rows}
                  </p>
                ) : null}
                <PhotoSheetCanvas photos={page.photos} layout={page.layout} />
                <p className="mt-2 text-center font-mono text-[10px] text-white/25">
                  间距 {page.layout.gapMm / 10} cm · 整块 {(page.layout.blockWidthMm / 10).toFixed(1)}×
                  {(page.layout.blockHeightMm / 10).toFixed(1)} cm · 居中于 A4
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {photos.length > 0 ? (
        <div className="photo-sheet-print-root print-only">
          {pages.map((page, i) => (
            <div
              key={page.pageIndex}
              className={`photo-sheet-print-page${i < pages.length - 1 ? " photo-sheet-print-page--break" : ""}`}
            >
              <PhotoSheetCanvas photos={page.photos} layout={page.layout} forPrint />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PhotoSheetCanvas({
  photos,
  layout,
  forPrint = false,
}: {
  photos: UploadedPhoto[];
  layout: ReturnType<typeof layoutPhotoSheet>;
  forPrint?: boolean;
}) {
  const cellW = cellWidthPercent();
  const cellH = cellHeightPercent();

  return (
    <div className={`photo-sheet-stage ${forPrint ? "photo-sheet-stage--print" : ""}`}>
      <div
        className="photo-sheet-a4"
        style={
          forPrint
            ? { width: `${A4_WIDTH_MM}mm`, height: `${A4_HEIGHT_MM}mm` }
            : undefined
        }
      >
        {photos.length === 0 ? (
          <div className="photo-sheet-empty">
            <span className="font-mono text-[10px] text-black/35">A4 · 210 × 297 mm</span>
          </div>
        ) : (
          layout.slots.map((slot) => {
            const photo = photos[slot.index];
            if (!photo) return null;
            return (
              <div
                key={photo.id}
                className="photo-sheet-cell"
                style={
                  forPrint
                    ? {
                        left: `${slot.xMm}mm`,
                        top: `${slot.yMm}mm`,
                        width: `${CELL_SIZE_MM}mm`,
                        height: `${CELL_SIZE_MM}mm`,
                      }
                    : {
                        left: `${mmToPercentX(slot.xMm)}%`,
                        top: `${mmToPercentY(slot.yMm)}%`,
                        width: `${cellW}%`,
                        height: `${cellH}%`,
                      }
                }
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={photo.url} alt={photo.name} className="photo-sheet-cell-img" draggable={false} />
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
