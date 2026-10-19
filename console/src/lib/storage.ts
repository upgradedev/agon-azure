/*
  Per-browser conveniences only (theme, a skipped guide). Storage can be
  blocked or empty in private windows, so every access is wrapped and the page
  works the same without it.
*/
export const THEME_KEY = "agon-theme";
export const GUIDE_KEY = "agon-guide";

export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStorage(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

export function removeStorage(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* Nothing to undo without storage. */
  }
}
