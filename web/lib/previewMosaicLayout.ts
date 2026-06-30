import type { GalleryItem } from "@/components/types";

export type PreviewOrient = "portrait" | "landscape" | "square" | "unknown";

export type PreviewMosaicItem<T> = {
  key: string;
  item: GalleryItem;
  data: T;
};

export type PreviewMosaicBand<T> =
  | { type: "two-portraits"; cells: [PreviewMosaicItem<T>, PreviewMosaicItem<T>] }
  | { type: "two-landscapes"; cells: [PreviewMosaicItem<T>, PreviewMosaicItem<T>] }
  | { type: "solo"; cell: PreviewMosaicItem<T>; orient: PreviewOrient }
  | {
      type: "portrait-and-two-landscapes";
      portrait: PreviewMosaicItem<T>;
      landscapes: [PreviewMosaicItem<T>, PreviewMosaicItem<T>];
    }
  | {
      type: "two-landscapes-and-portrait";
      landscapes: [PreviewMosaicItem<T>, PreviewMosaicItem<T>];
      portrait: PreviewMosaicItem<T>;
    };

const PORTRAIT_MAX_RATIO = 0.88;
const LANDSCAPE_MIN_RATIO = 1.12;

export function previewLayoutOrient(item: GalleryItem): PreviewOrient {
  let w = Number(item.width) || 0;
  let h = Number(item.height) || 0;
  const rot = Math.abs(Math.trunc(Number(item.rotate_degrees ?? 0)) % 360);
  if ((rot === 90 || rot === 270) && w > 0 && h > 0) {
    const t = w;
    w = h;
    h = t;
  }
  if (w > 0 && h > 0) {
    const r = w / h;
    if (r >= LANDSCAPE_MIN_RATIO) return "landscape";
    if (r <= PORTRAIT_MAX_RATIO) return "portrait";
    return "square";
  }
  const o = String(item.orientation ?? "").toLowerCase();
  if (o === "landscape" || o === "portrait" || o === "square") return o;
  return "unknown";
}

function packAsLandscape(o: PreviewOrient): PreviewOrient {
  return o === "portrait" ? o : o === "unknown" || o === "square" ? "landscape" : o;
}

function isPortrait(o: PreviewOrient): boolean {
  return o === "portrait";
}

/** 仅真实横图/方图可参与横图配对（不含 unknown，避免末尾缺项误判）。 */
function isLandscapeForPair(o: PreviewOrient): boolean {
  return o === "landscape" || o === "square";
}

/** 贪心组排：双竖 / 双横 / 一竖两横 / 两横一竖 / 单张全宽。 */
export function packPreviewMosaicBands<T>(cells: PreviewMosaicItem<T>[]): PreviewMosaicBand<T>[] {
  const bands: PreviewMosaicBand<T>[] = [];
  const n = cells.length;
  let i = 0;

  while (i < n) {
    const c0 = cells[i];
    if (!c0) {
      i += 1;
      continue;
    }
    const o0 = previewLayoutOrient(c0.item);
    const c1 = i + 1 < n ? cells[i + 1] : undefined;
    const c2 = i + 2 < n ? cells[i + 2] : undefined;
    const o1 = c1 ? previewLayoutOrient(c1.item) : null;
    const o2 = c2 ? previewLayoutOrient(c2.item) : null;

    if (c1 && o1 && isPortrait(o0) && isPortrait(o1)) {
      bands.push({ type: "two-portraits", cells: [c0, c1] });
      i += 2;
      continue;
    }

    if (c1 && o1 && isLandscapeForPair(o0) && isLandscapeForPair(o1)) {
      bands.push({ type: "two-landscapes", cells: [c0, c1] });
      i += 2;
      continue;
    }

    if (c1 && c2 && o1 && o2 && isPortrait(o0) && isLandscapeForPair(o1) && isLandscapeForPair(o2)) {
      bands.push({
        type: "portrait-and-two-landscapes",
        portrait: c0,
        landscapes: [c1, c2],
      });
      i += 3;
      continue;
    }

    if (c1 && c2 && o1 && o2 && isLandscapeForPair(o0) && isLandscapeForPair(o1) && isPortrait(o2)) {
      bands.push({
        type: "two-landscapes-and-portrait",
        landscapes: [c0, c1],
        portrait: c2,
      });
      i += 3;
      continue;
    }

    const soloOrient = isPortrait(o0) ? "portrait" : packAsLandscape(o0);
    bands.push({ type: "solo", cell: c0, orient: soloOrient });
    i += 1;
  }

  return bands;
}
