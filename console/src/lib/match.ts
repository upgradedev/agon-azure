/*
  The match analysis files (match/<seed>.json), written at deploy by
  agon.webmatch from the exact event logs the site serves and computed by
  agon.interpret and agon.governance.recompute. The browser only picks values
  and draws them: no football number is computed here.

  Loading: match/manifest.json, then the seed's file, re-hashed with SHA-256
  against the manifest, shape-checked, and refused unless its events_sha256
  equals the sha256 the data manifest lists for the event log being replayed.
*/
import { isInt, isObject } from "./core";
import { fetchBytes, sha256Hex } from "./hash";

export const MATCH_BASE = "match/";
export const MATCH_MANIFEST = `${MATCH_BASE}manifest.json`;
export const MANIFEST_SCHEMA = "agon.webmatch-manifest/1";
export const MATCH_SCHEMA = "agon.webmatch/1";
export const INTERPRET_DOC_URL = "https://github.com/upgradedev/agon-azure/blob/main/docs/INTERPRET.md";

export type Side = "home" | "away";
export const SIDES: Side[] = ["home", "away"];
export const SIDE_WORD: Record<Side, string> = { home: "Home", away: "Away" };

export type TimelineKind = "goal" | "save" | "block" | "shot_off" | "yellow" | "red" | "sub" | "corner" | "foul";
export const TIMELINE_KINDS: TimelineKind[] = ["goal", "save", "block", "shot_off", "yellow", "red", "sub", "corner", "foul"];

export interface MatchClub {
  club_id: string;
  name: string;
  short: string;
  kit_primary_hex: string;
  kit_secondary_hex: string;
}

export interface Goal {
  event_id: string;
  seq: number;
  side: Side;
  minute: string;
  at_s: number;
  player: string;
  name: string;
  penalty: boolean;
  score_after: { home: number; away: number };
}

export interface TimelineMark {
  event_id: string;
  seq: number;
  kind: TimelineKind;
  side: Side;
  minute: string;
  at_s: number;
  player: string | null;
  name: string | null;
  big_chance: boolean;
  penalty: boolean;
  second_yellow: boolean;
  related_name: string | null;
}

export interface LivePoint {
  seq: number;
  period: number;
  minute: string;
  at_s: number;
  home_possession_permille: number;
  home_xg_ppm: number;
  away_xg_ppm: number;
  home_shots: number;
  away_shots: number;
}

export interface WinPoint {
  period: number;
  minute: string;
  at_s: number;
  home_ppm: number;
  draw_ppm: number;
  away_ppm: number;
}

export interface TurningPoint {
  event_id: string;
  seq: number;
  reason: string;
  side: Side;
  minute: string;
  at_s: number;
  name: string | null;
  delta_home_ppm: number;
  weight_ppm: number;
}

export interface Shot {
  event_id: string;
  seq: number;
  side: Side;
  minute: string;
  at_s: number;
  player: string;
  name: string;
  outcome: "goal" | "saved" | "off_target" | "blocked";
  qualifier: string | null;
  x_dm: number;
  y_dm: number;
  xg_ppm: number;
  cum_xg_ppm: number;
  big_chance: boolean;
}

export interface MomentumPoint {
  period: number;
  minute: number;
  at_s: number;
  momentum_ppm: number;
}

export interface Band {
  window: string;
  period: number;
  from_s: number;
  to_s: number;
  control_permille: number | null;
  control_band: string | null;
  home_pressures: number;
  away_pressures: number;
  home_pressure_band: string | null;
  away_pressure_band: string | null;
  tempo_band: string | null;
}

export interface TeamStat {
  key: string;
  label: string;
  unit: "count" | "permille" | "ppm" | "percent" | "centi";
  better: "higher" | "lower";
  home: number | null;
  away: number | null;
}

export interface PlayerLine {
  player_id: string;
  side: Side;
  name: string;
  shirt: number;
  position: "GK" | "DF" | "MF" | "FW";
  started: boolean;
  minutes: number;
  goals: number;
  assists: number;
  yellow_cards: number;
  red_cards: number;
  shots: number;
  xg_ppm: number;
  xt_added_ppm: number;
  passes: number;
  passes_completed: number;
  pass_accuracy_pct: number | null;
  mean_pass_distance_dm: number | null;
  mean_pass_difficulty_centi: number | null;
  zones: [number, number][];
  top_carry_proxy_kmh_x10: number | null;
  sprint_carries: number;
  distance_dm: number | null;
}

export interface MatchFile {
  schema: string;
  generator: string;
  interpret: string;
  model_sha256: string;
  seed: number;
  match_id: string;
  events_sha256: string;
  world_sha256: string;
  events: number;
  last_seq: number;
  clubs: Record<Side, MatchClub>;
  axis: { half_offset_s: number; half_time_s: number; end_s: number };
  score: { home: number; away: number };
  goals: Goal[];
  timeline: TimelineMark[];
  live: LivePoint[];
  win_probability: WinPoint[];
  turning_points: TurningPoint[];
  shots: Shot[];
  momentum: MomentumPoint[];
  bands: Band[];
  territory: { cols: number; rows: number; home: number[]; away: number[] };
  team_stats: TeamStat[];
  ppda_by_15: { bin: string; home_ppda_centi: number | null; away_ppda_centi: number | null }[];
  players: PlayerLine[];
  star: { player_id: string; side: Side; name: string } | null;
  rules: { possession: string; big_chance: string; star: string; speed: string; distance: string };
}

interface ManifestFile {
  name: string;
  seed: number;
  sha256: string;
  events_sha256: string;
}

const SHA = /^[0-9a-f]{64}$/;
const TOP_KEYS = [
  "schema", "generator", "interpret", "model_sha256", "seed", "match_id", "events_sha256", "world_sha256", "events", "last_seq",
  "clubs", "axis", "score", "goals", "timeline", "live", "win_probability", "turning_points", "shots", "momentum", "bands",
  "territory", "team_stats", "ppda_by_15", "players", "star", "rules",
];
const LIST_KEYS = ["goals", "timeline", "live", "win_probability", "turning_points", "shots", "momentum", "bands", "team_stats", "ppda_by_15", "players"];

function isSide(value: unknown): value is Side {
  return value === "home" || value === "away";
}

/* The shape the drawing code relies on; agon.webmatch.validate_match holds the full contract. */
export function validateMatchFile(body: unknown): string[] {
  if (!isObject(body)) {
    return ["the match file is not an object"];
  }
  const problems: string[] = [];
  const keys = Object.keys(body).sort();
  if (keys.join(",") !== [...TOP_KEYS].sort().join(",")) {
    problems.push("the match file keys differ from agon.webmatch/1");
    return problems;
  }
  if (body.schema !== MATCH_SCHEMA) {
    problems.push(`schema must be ${MATCH_SCHEMA}`);
  }
  if (typeof body.events_sha256 !== "string" || !SHA.test(body.events_sha256)) {
    problems.push("events_sha256 must be 64 lowercase hex characters");
  }
  for (const key of LIST_KEYS) {
    if (!Array.isArray(body[key])) {
      problems.push(`${key} must be a list`);
    }
  }
  const clubs = body.clubs;
  if (!isObject(clubs) || !SIDES.every((s) => isObject(clubs[s]) && typeof (clubs[s] as Record<string, unknown>).name === "string")) {
    problems.push("clubs must name the home and the away club");
  }
  const axis = body.axis;
  if (!isObject(axis) || !["half_offset_s", "half_time_s", "end_s"].every((k) => isInt(axis[k]))) {
    problems.push("axis must hold half_offset_s, half_time_s and end_s");
  }
  const t = body.territory;
  if (
    !isObject(t) ||
    !isInt(t.cols) ||
    !isInt(t.rows) ||
    !SIDES.every((s) => Array.isArray(t[s]) && (t[s] as unknown[]).length === Number(t.cols) * Number(t.rows) && (t[s] as unknown[]).every(isInt))
  ) {
    problems.push("territory must hold cols x rows counts for each side");
  }
  if (problems.length) {
    return problems;
  }
  for (const [i, row] of (body.win_probability as unknown[]).entries()) {
    if (!isObject(row) || Number(row.home_ppm) + Number(row.draw_ppm) + Number(row.away_ppm) !== 1_000_000) {
      problems.push(`win_probability[${i}] must sum to 1000000 ppm`);
      break;
    }
  }
  for (const key of ["goals", "timeline", "shots", "turning_points", "players"]) {
    if (!(body[key] as unknown[]).every((row) => isObject(row) && isSide(row.side))) {
      problems.push(`every ${key} row must name its side, home or away`);
    }
  }
  if (!(body.timeline as unknown[]).every((row) => isObject(row) && TIMELINE_KINDS.includes(row.kind as TimelineKind))) {
    problems.push("every timeline row must have a known kind");
  }
  for (const key of ["live", "win_probability", "shots", "momentum", "timeline"]) {
    if (!(body[key] as unknown[]).every((row) => isObject(row) && isInt(row.at_s))) {
      problems.push(`every ${key} row needs an integer at_s`);
    }
  }
  return problems;
}

export type AnalysisLoad =
  | { status: "loading" }
  | { status: "missing"; message: string }
  | { status: "mismatch"; message: string }
  | { status: "error"; message: string }
  | { status: "ready"; file: MatchFile; sha256: string };

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/* Fetch, re-hash and check one seed's analysis. `eventsSha256` is the data
   manifest's sha256 for the event log the replay is drawing. */
export async function loadAnalysis(seed: number, eventsSha256: string): Promise<AnalysisLoad> {
  let manifest: unknown;
  try {
    manifest = JSON.parse((await fetchBytes(MATCH_MANIFEST)).text);
  } catch (error) {
    const text = message(error);
    if (/answered 404|returned a web page/.test(text)) {
      return {
        status: "missing",
        message: "This build has no match analysis files: ci/stamp_web.py writes them at deploy, from the same event logs the replay reads.",
      };
    }
    return { status: "error", message: `The match analysis list could not be read: ${text}.` };
  }
  if (!isObject(manifest) || manifest.schema !== MANIFEST_SCHEMA || !Array.isArray(manifest.files)) {
    return { status: "error", message: `match/manifest.json is not an ${MANIFEST_SCHEMA} list.` };
  }
  const entry = (manifest.files as unknown[]).find((f): f is ManifestFile => isObject(f) && f.seed === seed) as ManifestFile | undefined;
  if (!entry || typeof entry.name !== "string" || !/^[0-9]{1,9}\.json$/.test(entry.name) || !SHA.test(String(entry.sha256))) {
    return { status: "missing", message: `The match analysis list has no file for seed ${seed}.` };
  }
  if (entry.events_sha256 !== eventsSha256) {
    return {
      status: "mismatch",
      message: `The match analysis for seed ${seed} was computed from another event log than the one this replay reads, so it is not drawn. This happens with the small development fixture.`,
    };
  }
  try {
    const fetched = await fetchBytes(`${MATCH_BASE}${entry.name}`);
    const sha256 = await sha256Hex(fetched.buffer);
    if (sha256 !== entry.sha256) {
      return { status: "error", message: `match/${entry.name} hashes to ${sha256.slice(0, 12)}, the list says ${entry.sha256.slice(0, 12)}; it is not drawn.` };
    }
    const body: unknown = JSON.parse(fetched.text);
    const problems = validateMatchFile(body);
    if (problems.length) {
      return { status: "error", message: `match/${entry.name} breaks the match analysis contract: ${problems.slice(0, 3).join("; ")}.` };
    }
    const file = body as MatchFile;
    if (file.events_sha256 !== eventsSha256 || file.seed !== seed) {
      return { status: "mismatch", message: `match/${entry.name} describes another event log than the replay, so it is not drawn.` };
    }
    return { status: "ready", file, sha256 };
  } catch (error) {
    return { status: "error", message: `The match analysis for seed ${seed} could not be read: ${message(error)}.` };
  }
}

/* ------------------------------------------------------------- picking and formatting */

/* The last per-minute point at or before the replay's current event. */
export function liveAt(file: MatchFile, seq: number | null): LivePoint | null {
  if (seq === null) {
    return null;
  }
  let found: LivePoint | null = null;
  for (const point of file.live) {
    if (point.seq <= seq) {
      found = point;
    } else {
      break;
    }
  }
  return found;
}

/* Where an event sits on the drawn time axis (the second half starts where the first ended). */
export function axisSeconds(file: MatchFile, period: number, minute: number, second: number): number {
  return minute * 60 + second + (period === 2 ? file.axis.half_offset_s : 0);
}

export function xg(ppm: number): string {
  return (ppm / 1_000_000).toFixed(2);
}

export function percentOfPermille(permille: number): string {
  return `${Math.round(permille / 10)}%`;
}

export function probability(ppm: number): string {
  return `${Math.round(ppm / 10_000)}%`;
}

export function statValue(stat: TeamStat, side: Side): string {
  const value = stat[side];
  if (value === null) {
    return "none";
  }
  switch (stat.unit) {
    case "permille":
      return percentOfPermille(value);
    case "ppm":
      return xg(value);
    case "percent":
      return `${value}%`;
    case "centi":
      return (value / 100).toFixed(2);
    default:
      return String(value);
  }
}

export function kmh(x10: number | null): string {
  return x10 === null ? "none" : `${(x10 / 10).toFixed(1)} km/h`;
}

export function windowWords(window: string): string {
  const m = /^p([12]) ([0-9]+)-([0-9]+)$/.exec(window);
  if (!m) {
    return window;
  }
  return `${m[1] === "1" ? "1st" : "2nd"} half, minutes ${m[2]} to ${m[3]}`;
}

export const KIND_WORDS: Record<TimelineKind, string> = {
  goal: "Goal",
  save: "Shot saved",
  block: "Shot blocked",
  shot_off: "Shot off target",
  yellow: "Yellow card",
  red: "Red card",
  sub: "Substitution",
  corner: "Corner",
  foul: "Foul",
};

export const REASON_WORDS: Record<string, string> = {
  goal: "Goal",
  sending_off: "Sending-off",
  penalty: "Penalty",
  big_chance: "Big chance",
};
