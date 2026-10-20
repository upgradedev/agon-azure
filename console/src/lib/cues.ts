/*
  Timed cues: machine-readable overlay lines timed to the replay (not a live
  or real-time feed), published per seed as cues/<seed>.json under an
  approved charter hash. ci/stamp_web.py
  lists the cue files present in cues/manifest.json (an empty list until a
  charter is approved); the console reads the list first and fetches a seed's
  file only when it is listed, re-hashing it against the list. Until a cue file
  exists the Match Centre says so, and draws nothing in its place.

  Expected shape (agon.cues/1):
    {"schema": "agon.cues/1", "seed": int, "charter_sha256": 64 hex,
     "cues": [{"at_s": int, "until_s": int, "kind": str,
               "text": {"en": str, "el"?: str, "es"?: str, "fr"?: str, "easy"?: str}}]}
  at_s and until_s are on the same drawn time axis as the match analysis
  (match seconds, the second half starting where the first half ended).
*/
import { isInt, isObject } from "./core";
import { fetchBytes, sha256Hex } from "./hash";
import type { Language } from "../hooks/preferences";

export const CUES_SCHEMA = "agon.cues/1";

export interface Cue {
  at_s: number;
  until_s: number;
  kind: string;
  text: Partial<Record<Language, string>> & { en: string };
}

export type CueLoad =
  | { status: "loading" }
  | { status: "missing"; message: string }
  | { status: "error"; message: string }
  | { status: "ready"; charter: string; cues: Cue[] };

const SHA = /^[0-9a-f]{64}$/;

export function validateCues(body: unknown, seed: number): string[] {
  if (!isObject(body) || body.schema !== CUES_SCHEMA) {
    return [`the cue file is not ${CUES_SCHEMA}`];
  }
  const problems: string[] = [];
  if (body.seed !== seed) {
    problems.push(`the cue file is for seed ${String(body.seed)}, not ${seed}`);
  }
  if (typeof body.charter_sha256 !== "string" || !SHA.test(body.charter_sha256)) {
    problems.push("the cue file names no approved charter hash");
  }
  if (!Array.isArray(body.cues)) {
    problems.push("cues must be a list");
    return problems;
  }
  for (const [i, cue] of (body.cues as unknown[]).entries()) {
    if (
      !isObject(cue) ||
      !isInt(cue.at_s) ||
      !isInt(cue.until_s) ||
      cue.until_s < cue.at_s ||
      typeof cue.kind !== "string" ||
      !isObject(cue.text) ||
      typeof cue.text.en !== "string"
    ) {
      problems.push(`cues[${i}] needs at_s, until_s, kind and an English text`);
      break;
    }
  }
  return problems;
}

const NONE = (seed: number): CueLoad => ({
  status: "missing",
  message: `Timed cues: there is no approved charter and no cue file for seed ${seed} yet, so no lower-third is shown.`,
});

export async function loadCues(seed: number): Promise<CueLoad> {
  try {
    const list: unknown = JSON.parse((await fetchBytes("cues/manifest.json")).text);
    if (!isObject(list) || list.schema !== "agon.cues-manifest/1" || !Array.isArray(list.files)) {
      return { status: "error", message: "cues/manifest.json is not an agon.cues-manifest/1 list." };
    }
    const entry = (list.files as unknown[]).find((f) => isObject(f) && f.seed === seed);
    if (!isObject(entry) || typeof entry.name !== "string" || typeof entry.sha256 !== "string" || !/^[0-9]{1,9}\.json$/.test(entry.name)) {
      return NONE(seed);
    }
    const fetched = await fetchBytes(`cues/${entry.name}`);
    if ((await sha256Hex(fetched.buffer)) !== entry.sha256) {
      return { status: "error", message: `cues/${entry.name} does not hash to its list entry, so it is not drawn.` };
    }
    const body: unknown = JSON.parse(fetched.text);
    const problems = validateCues(body, seed);
    if (problems.length) {
      return { status: "error", message: `cues/${seed}.json is not drawn: ${problems.join("; ")}.` };
    }
    const b = body as { charter_sha256: string; cues: Cue[] };
    return { status: "ready", charter: b.charter_sha256, cues: b.cues };
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    if (/answered 404|returned a web page/.test(text)) {
      return NONE(seed);
    }
    return { status: "error", message: `Timed cues could not be read: ${text}.` };
  }
}

export function cueAt(cues: Cue[], nowS: number | null): Cue | null {
  if (nowS === null) {
    return null;
  }
  let found: Cue | null = null;
  for (const cue of cues) {
    if (cue.at_s <= nowS && nowS < cue.until_s) {
      found = cue;
    }
  }
  return found;
}

export function cueText(cue: Cue, language: Language): { text: string; language: Language } {
  const own = cue.text[language];
  return own ? { text: own, language } : { text: cue.text.en, language: "en" };
}
