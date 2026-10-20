/*
  The viewer's own view of a match, remembered per browser: Fan or Analyst
  mode, a favourite club and player, following that player through the match,
  one metric to tell the match through, and a language for the report editions
  and any timed cues on the replay. Storage can be blocked (private windows), so every access
  goes through lib/storage (try/catch) and the page works the same without it.
  The server renders the defaults; the browser's choices apply after hydration.
*/
import { useSyncExternalStore } from "react";
import { readStorage, writeStorage } from "../lib/storage";

export type ViewMode = "fan" | "analyst";
export type MetricId = "xg" | "win" | "possession" | "ppda" | "pressure" | "shots";
export type Language = "en" | "el" | "es" | "fr" | "easy";

export interface Preferences {
  mode: ViewMode;
  club: string | null;
  player: string | null;
  focus: boolean;
  metric: MetricId | null;
  language: Language;
}

export const PREFS_KEY = "agon-view";
export const DEFAULT_PREFS: Preferences = { mode: "fan", club: null, player: null, focus: false, metric: null, language: "en" };
const MODES: ViewMode[] = ["fan", "analyst"];
export const METRICS: MetricId[] = ["xg", "win", "possession", "ppda", "pressure", "shots"];
export const LANGUAGES: Language[] = ["en", "el", "es", "fr", "easy"];
const ID = /^C[0-9]{1,2}(P[0-9]{2})?$/;

function parse(raw: string | null): Preferences {
  if (!raw) {
    return DEFAULT_PREFS;
  }
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    return {
      mode: MODES.includes(value.mode as ViewMode) ? (value.mode as ViewMode) : DEFAULT_PREFS.mode,
      club: typeof value.club === "string" && ID.test(value.club) ? value.club : null,
      player: typeof value.player === "string" && ID.test(value.player) ? value.player : null,
      focus: value.focus === true,
      metric: METRICS.includes(value.metric as MetricId) ? (value.metric as MetricId) : null,
      language: LANGUAGES.includes(value.language as Language) ? (value.language as Language) : DEFAULT_PREFS.language,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

const listeners = new Set<() => void>();
let cached: { raw: string | null; prefs: Preferences } | null = null;

function snapshot(): Preferences {
  const raw = readStorage(PREFS_KEY);
  if (!cached || cached.raw !== raw) {
    cached = { raw, prefs: parse(raw) };
  }
  return cached.prefs;
}

function subscribe(callback: () => void): () => void {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export function usePreferences(): Preferences {
  return useSyncExternalStore(subscribe, snapshot, () => DEFAULT_PREFS);
}

/* Without storage the choice still applies for this page view. */
let memory: Preferences | null = null;

export function setPreferences(change: Partial<Preferences>): void {
  const next = { ...(memory ?? snapshot()), ...change };
  memory = next;
  const raw = JSON.stringify(next);
  if (!writeStorage(PREFS_KEY, raw)) {
    cached = { raw: readStorage(PREFS_KEY), prefs: next };
  }
  listeners.forEach((listener) => listener());
}
