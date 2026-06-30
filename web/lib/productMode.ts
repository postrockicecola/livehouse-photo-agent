export type ProductMode = "professional" | "personal";

const STORAGE_KEY = "livehouse.productMode";
const REMEMBER_KEY = "livehouse.productModeRemember";

export function readProductMode(): ProductMode | null {
  if (typeof window === "undefined") return null;
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "professional" || v === "personal") return v;
  } catch {
    /* ignore */
  }
  return null;
}

export function readRememberProductMode(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(REMEMBER_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveProductMode(mode: ProductMode, remember: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
    localStorage.setItem(REMEMBER_KEY, remember ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export function clearProductModePref(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(REMEMBER_KEY);
  } catch {
    /* ignore */
  }
}

export function productModeHref(mode: ProductMode): string {
  return mode === "professional" ? "/studio" : "/personal";
}

export function productModeLabel(mode: ProductMode): string {
  return mode === "professional" ? "专业版" : "个人版";
}
