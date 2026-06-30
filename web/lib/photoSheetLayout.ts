/** A4 sheet layout for 6.5×6.5 cm square photo cells with cut margins. */

export const A4_WIDTH_MM = 210;
export const A4_HEIGHT_MM = 297;
export const CELL_SIZE_MM = 65; // 6.5 cm
/** White space between photos for scissors / hand cutting (print). */
export const CELL_GAP_MM = 10; // 1 cm
export const MAX_PHOTOS = 9;
export const MIN_PHOTOS = 1;

export type SheetSlot = {
  index: number;
  xMm: number;
  yMm: number;
  sizeMm: number;
};

export type SheetLayout = {
  count: number;
  cols: number;
  rows: number;
  gapMm: number;
  blockWidthMm: number;
  blockHeightMm: number;
  slots: SheetSlot[];
};

export type A4GridLimits = {
  maxCols: number;
  maxRows: number;
  maxPerPage: number;
};

function blockSizeMm(cols: number, rows: number): { w: number; h: number } {
  const gap = cols > 1 || rows > 1 ? CELL_GAP_MM : 0;
  return {
    w: cols * CELL_SIZE_MM + Math.max(0, cols - 1) * gap,
    h: rows * CELL_SIZE_MM + Math.max(0, rows - 1) * gap,
  };
}

function fitsOnA4(cols: number, rows: number): boolean {
  const { w, h } = blockSizeMm(cols, rows);
  return w <= A4_WIDTH_MM + 0.01 && h <= A4_HEIGHT_MM + 0.01;
}

function cellStepMm(): number {
  return CELL_SIZE_MM + CELL_GAP_MM;
}

/** How many 6.5 cm cells (+ 1 cm gap) fit on one A4 sheet. */
export function maxGridForA4(): A4GridLimits {
  let maxCols = 1;
  for (let c = 1; c <= 4; c++) {
    if (fitsOnA4(c, 1)) maxCols = c;
  }
  let maxRows = 1;
  for (let r = 1; r <= 6; r++) {
    if (fitsOnA4(1, r)) maxRows = r;
  }
  return { maxCols, maxRows, maxPerPage: maxCols * maxRows };
}

function gridDimensions(count: number): { cols: number; rows: number } {
  const n = Math.max(0, Math.min(MAX_PHOTOS, count));
  if (n === 0) return { cols: 0, rows: 0 };

  const { maxCols, maxRows } = maxGridForA4();
  // Row-first: fixed column count (full width), fill top → bottom, left → right.
  const cols = Math.min(maxCols, n);
  const rows = Math.ceil(n / cols);
  if (rows <= maxRows && fitsOnA4(cols, rows)) {
    return { cols, rows };
  }

  let bestCols = 1;
  let bestRows = 1;
  let bestArea = Infinity;
  for (let c = 1; c <= Math.min(maxCols, n); c++) {
    const rowsNeeded = Math.ceil(n / c);
    if (rowsNeeded > maxRows) continue;
    if (!fitsOnA4(c, rowsNeeded)) continue;
    const area = c * rowsNeeded;
    if (area >= n && area < bestArea) {
      bestArea = area;
      bestCols = c;
      bestRows = rowsNeeded;
    }
  }
  return { cols: bestCols, rows: bestRows };
}

/** Split across A4 pages on full-row boundaries (e.g. 9 → 6 + 3, not 5 + 4). */
export function paginatePhotoCounts(total: number): number[] {
  if (total <= 0) return [];
  const { maxCols, maxPerPage } = maxGridForA4();
  if (total <= maxPerPage) return [total];

  const counts: number[] = [];
  let remaining = total;

  while (remaining > 0) {
    if (remaining <= maxPerPage) {
      counts.push(remaining);
      break;
    }

    let take = Math.floor(maxPerPage / maxCols) * maxCols;
    if (take <= 0) take = maxPerPage;

    const left = remaining - take;
    if (left > 0 && left < maxCols) {
      take -= maxCols;
    }
    if (take <= 0) {
      take = Math.min(remaining, maxCols);
    }

    counts.push(take);
    remaining -= take;
  }

  return counts;
}

/** Compute slot positions on A4; rows fill left → right, block centered on sheet. */
export function layoutPhotoSheet(count: number): SheetLayout {
  const n = Math.max(0, Math.min(MAX_PHOTOS, count));
  if (n === 0) {
    return { count: 0, cols: 0, rows: 0, gapMm: CELL_GAP_MM, blockWidthMm: 0, blockHeightMm: 0, slots: [] };
  }

  const { cols, rows } = gridDimensions(n);
  const { w: blockWidthMm, h: blockHeightMm } = blockSizeMm(cols, rows);
  const blockOffsetXMm = (A4_WIDTH_MM - blockWidthMm) / 2;
  const blockOffsetYMm = (A4_HEIGHT_MM - blockHeightMm) / 2;
  const step = cellStepMm();

  const slots: SheetSlot[] = [];
  let idx = 0;
  for (let row = 0; row < rows && idx < n; row++) {
    const itemsInRow = Math.min(cols, n - idx);
    for (let col = 0; col < itemsInRow; col++) {
      slots.push({
        index: idx,
        xMm: blockOffsetXMm + col * step,
        yMm: blockOffsetYMm + row * step,
        sizeMm: CELL_SIZE_MM,
      });
      idx++;
    }
  }

  return { count: n, cols, rows, gapMm: CELL_GAP_MM, blockWidthMm, blockHeightMm, slots };
}

export function mmToPercentX(mm: number): number {
  return (mm / A4_WIDTH_MM) * 100;
}

export function mmToPercentY(mm: number): number {
  return (mm / A4_HEIGHT_MM) * 100;
}

export function cellWidthPercent(): number {
  return (CELL_SIZE_MM / A4_WIDTH_MM) * 100;
}

export function cellHeightPercent(): number {
  return (CELL_SIZE_MM / A4_HEIGHT_MM) * 100;
}
