/*
  Browser state read through useSyncExternalStore.

  Each hook has a server snapshot that the prerender uses and the first client
  render repeats, so hydration sees the same markup; React then re-renders
  with the real browser value. That is how the hash route, the theme, a
  skipped guide and the motion preference reach the page without a hydration
  mismatch.

  The stamped commit is the exception: ci/stamp_web.py writes it into the
  prerendered build label at deploy time, so in the browser the hydration
  snapshot reads <body data-commit> as well (lib/commit.ts). An unstamped
  page reads as null in both, which is what the prerender wrote.
*/
import { useSyncExternalStore } from "react";
import { HOME, parseHash, type Route } from "../lib/router";
import { GUIDE_KEY, THEME_KEY, readStorage, removeStorage, writeStorage } from "../lib/storage";
import { stampedCommit } from "../lib/commit";

/* ------------------------------------------------------------- route */

let lastRoute: Route = HOME;
let lastHash: string | null = null;

function routeSnapshot(): Route {
  const hash = window.location.hash;
  if (hash !== lastHash) {
    lastHash = hash;
    const parsed = parseHash(hash);
    if (parsed) {
      lastRoute = parsed;
    }
  }
  return lastRoute;
}

function subscribeHash(callback: () => void): () => void {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribeHash, routeSnapshot, () => HOME);
}

export function navigate(href: string): void {
  if (window.location.hash === href) {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    return;
  }
  window.location.hash = href;
}

/* ------------------------------------------------------------- theme */

export type Theme = "light" | "dark";
const themeListeners = new Set<() => void>();
const darkQuery = (): MediaQueryList | null =>
  typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

function themeSnapshot(): Theme {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit === "light" || explicit === "dark") {
    return explicit;
  }
  return darkQuery()?.matches ? "dark" : "light";
}

function subscribeTheme(callback: () => void): () => void {
  themeListeners.add(callback);
  const query = darkQuery();
  query?.addEventListener("change", callback);
  return () => {
    themeListeners.delete(callback);
    query?.removeEventListener("change", callback);
  };
}

/* null on the server: the toggle then renders a neutral label. */
export function useTheme(): Theme | null {
  return useSyncExternalStore<Theme | null>(subscribeTheme, themeSnapshot, () => null);
}

export function setTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  writeStorage(THEME_KEY, theme);
  themeListeners.forEach((listener) => listener());
}

/* ------------------------------------------------------------- commit */

function noSubscribe(): () => void {
  return () => undefined;
}

function commitSnapshot(): string | null {
  return stampedCommit(document.body.getAttribute("data-commit"));
}

export function useCommit(): string | null {
  return useSyncExternalStore(noSubscribe, commitSnapshot, () => (typeof document === "undefined" ? null : commitSnapshot()));
}

/* ------------------------------------------------------------- guide */

const guideListeners = new Set<() => void>();

function subscribeGuide(callback: () => void): () => void {
  guideListeners.add(callback);
  return () => guideListeners.delete(callback);
}

export function useGuideSkipped(): boolean {
  return useSyncExternalStore(
    subscribeGuide,
    () => readStorage(GUIDE_KEY) === "skipped" || document.documentElement.getAttribute("data-guide") === "skipped",
    () => false,
  );
}

export function setGuideSkipped(skipped: boolean): void {
  if (skipped) {
    writeStorage(GUIDE_KEY, "skipped");
    document.documentElement.setAttribute("data-guide", "skipped");
  } else {
    removeStorage(GUIDE_KEY);
    document.documentElement.removeAttribute("data-guide");
  }
  guideListeners.forEach((listener) => listener());
}

/* ------------------------------------------------------------- motion */

function motionQuery(): MediaQueryList | null {
  return typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
}

function subscribeMotion(callback: () => void): () => void {
  const query = motionQuery();
  query?.addEventListener("change", callback);
  return () => query?.removeEventListener("change", callback);
}

export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeMotion, () => Boolean(motionQuery()?.matches), () => false);
}

/* ------------------------------------------------------------- viewport */

const WIDE_QUERY = "(min-width: 1024px)";

function wideQuery(): MediaQueryList | null {
  return typeof window !== "undefined" && window.matchMedia ? window.matchMedia(WIDE_QUERY) : null;
}

function subscribeWide(callback: () => void): () => void {
  const query = wideQuery();
  query?.addEventListener("change", callback);
  return () => query?.removeEventListener("change", callback);
}

/* True at the lg breakpoint and up. The server assumes a wide screen. */
export function useWide(): boolean {
  return useSyncExternalStore(subscribeWide, () => Boolean(wideQuery()?.matches), () => true);
}
