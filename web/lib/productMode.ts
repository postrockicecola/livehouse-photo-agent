export type ProductMode = "professional";

const STORAGE_KEY = "livehouse.productMode";
const REMEMBER_KEY = "livehouse.productModeRemember";

export function readProductMode(): ProductMode | null {
  if (typeof window === "undefined") return null;
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    // Migrate legacy "personal" preference → professional.
    if (v === "professional" || v === "personal") return "professional";
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

export function productModeHref(_mode: ProductMode): string {
  return "/studio";
}

export function productModeLabel(_mode: ProductMode): string {
  return "专业版";
}
