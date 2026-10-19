/*
  Match data rules for the Match Centre and the Verify page.

  Pure functions, no DOM. The rules match agon.webdata.validate_manifest and
  validate_world, and the frame conversion matches
  agon.synth.schema.to_fixed_frame. tests/core.test.ts holds this module to the
  same case lists the Python validators are held to (tests/js/*.json at the
  repository root) and to the frame golden.
*/
import type { Club, DataManifest, ManifestEntry, MatchEvent, Player, World } from "./types";

export const PITCH_X_DM = 1050;
export const PITCH_Y_DM = 680;
const HALF_SECONDS = 2700;
export const DATA_BASE = "data/";
export const FIXTURE_BASE = "data-fixture/";
export const MANIFEST_NAME = "manifest.json";

const MANIFEST_KEYS = ["files", "generator"];
const FILE_KEYS = ["events", "name", "seed", "sha256", "world", "world_sha256"];
const WORLD_KEYS = ["away", "clubs", "generator", "home", "league", "match_id", "seed"];
const CLUB_KEYS = ["club_id", "kit_primary", "kit_primary_hex", "kit_secondary", "kit_secondary_hex", "name", "players", "short"];
const PLAYER_KEYS = ["name", "player_id", "position", "shirt"];
const FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*\.ndjson$/;
const WORLD_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*\.world\.json$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const CLUB_ID = /^C[0-9]{1,2}$/;
const PLAYER_ID = /^C[0-9]{1,2}P[0-9]{2}$/;
const SHORT_CODE = /^[A-Z]{3}$/;
const HEX_COLOUR = /^#[0-9a-f]{6}$/;
const POSITIONS = new Set(["GK", "DF", "MF", "FW"]);
const ON_BALL = new Set(["pass", "carry", "shot"]);
export const PRESSURE_WINDOW_SECONDS = 300;

type Json = Record<string, unknown>;

export function isInt(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && Math.floor(value) === value;
}

export function isObject(value: unknown): value is Json {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sameKeys(obj: Json, expected: string[]): boolean {
  const keys = Object.keys(obj).sort();
  const want = [...expected].sort();
  return keys.length === want.length && keys.every((key, i) => key === want[i]);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.replace(/\s+/g, "") !== "";
}

/* ------------------------------------------------------------- manifest */

export function isSafeFileName(name: unknown): name is string {
  return typeof name === "string" && FILE_NAME.test(name);
}

export function isSafeWorldName(name: unknown): name is string {
  return typeof name === "string" && WORLD_NAME.test(name);
}

/* Same rules and wording as agon.webdata.validate_manifest. */
export function validateManifest(manifest: unknown): string[] {
  if (!isObject(manifest)) {
    return ["manifest must be a JSON object"];
  }
  const problems: string[] = [];
  if (!sameKeys(manifest, MANIFEST_KEYS)) {
    problems.push("manifest keys must be exactly files and generator");
  }
  if (typeof manifest.generator !== "string" || !manifest.generator) {
    problems.push("generator must be a non-empty string");
  }
  const files = manifest.files;
  if (!Array.isArray(files) || files.length === 0) {
    problems.push("files must be a non-empty list");
    return problems;
  }
  const seen = new Set<string>();
  const seenSeeds = new Set<number>();
  files.forEach((entry: unknown, i: number) => {
    const where = `files[${i}]`;
    if (!isObject(entry)) {
      problems.push(`${where} must be an object`);
      return;
    }
    if (!sameKeys(entry, FILE_KEYS)) {
      problems.push(`${where} keys must be exactly events, name, seed, sha256, world and world_sha256`);
      return;
    }
    if (!isSafeFileName(entry.name)) {
      problems.push(`${where}.name must be a bare <seed>.ndjson file name`);
    } else if (seen.has(entry.name)) {
      problems.push(`${where}.name ${entry.name} is listed twice`);
    } else {
      seen.add(entry.name);
    }
    if (typeof entry.sha256 !== "string" || !SHA256_HEX.test(entry.sha256)) {
      problems.push(`${where}.sha256 must be 64 lowercase hex characters`);
    }
    if (!isInt(entry.seed) || entry.seed < 0) {
      problems.push(`${where}.seed must be a non-negative integer`);
    } else {
      const expected = `${entry.seed}.ndjson`;
      if (typeof entry.name === "string" && entry.name !== expected) {
        problems.push(`${where}.name ${entry.name} does not match seed ${entry.seed}, expected ${expected}`);
      }
      if (seenSeeds.has(entry.seed)) {
        problems.push(`${where}.seed ${entry.seed} is listed twice`);
      } else {
        seenSeeds.add(entry.seed);
      }
    }
    if (!isInt(entry.events) || entry.events < 1) {
      problems.push(`${where}.events must be a positive integer`);
    }
    if (!isSafeWorldName(entry.world)) {
      problems.push(`${where}.world must be a bare <seed>.world.json file name`);
    } else if (isInt(entry.seed) && entry.world !== `${entry.seed}.world.json`) {
      problems.push(`${where}.world ${entry.world} does not match seed ${entry.seed}, expected ${entry.seed}.world.json`);
    }
    if (typeof entry.world_sha256 !== "string" || !SHA256_HEX.test(entry.world_sha256)) {
      problems.push(`${where}.world_sha256 must be 64 lowercase hex characters`);
    }
  });
  return problems;
}

/* Same rules and wording as agon.webdata.validate_world. */
export function validateWorld(world: unknown): string[] {
  if (!isObject(world)) {
    return ["world must be a JSON object"];
  }
  if (!sameKeys(world, WORLD_KEYS)) {
    return [`world keys must be exactly ${WORLD_KEYS.join(", ")}`];
  }
  const problems: string[] = [];
  for (const key of ["generator", "match_id", "league"]) {
    if (!nonEmptyString(world[key])) {
      problems.push(`world.${key} must be a non-empty string`);
    }
  }
  if (!isInt(world.seed) || world.seed < 0) {
    problems.push("world.seed must be a non-negative integer");
  }
  const clubs = world.clubs;
  if (!Array.isArray(clubs) || clubs.length < 2) {
    problems.push("world.clubs must list at least two clubs");
    return problems;
  }
  const clubIds = new Set<string>();
  const playerIds = new Set<string>();
  clubs.forEach((club: unknown, i: number) => {
    const where = `clubs[${i}]`;
    if (!isObject(club) || !sameKeys(club, CLUB_KEYS)) {
      problems.push(`${where} keys must be exactly ${CLUB_KEYS.join(", ")}`);
      return;
    }
    let cid = "";
    if (typeof club.club_id !== "string" || !CLUB_ID.test(club.club_id)) {
      problems.push(`${where}.club_id must look like C1`);
    } else if (clubIds.has(club.club_id)) {
      problems.push(`${where}.club_id ${club.club_id} is listed twice`);
      cid = club.club_id;
    } else {
      cid = club.club_id;
      clubIds.add(cid);
    }
    if (!nonEmptyString(club.name)) {
      problems.push(`${where}.name must be a non-empty string`);
    }
    if (typeof club.short !== "string" || !SHORT_CODE.test(club.short)) {
      problems.push(`${where}.short must be three capital letters`);
    }
    for (const key of ["kit_primary", "kit_secondary"]) {
      const value = club[key];
      if (typeof value !== "string" || !value) {
        problems.push(`${where}.${key} must be a non-empty string`);
      }
      const hex = club[`${key}_hex`];
      if (typeof hex !== "string" || !HEX_COLOUR.test(hex)) {
        problems.push(`${where}.${key}_hex must be #rrggbb in lowercase hex`);
      }
    }
    const players = club.players;
    if (!Array.isArray(players) || players.length === 0) {
      problems.push(`${where}.players must be a non-empty list`);
      return;
    }
    const shirts = new Set<number>();
    players.forEach((player: unknown, j: number) => {
      const pwhere = `${where}.players[${j}]`;
      if (!isObject(player) || !sameKeys(player, PLAYER_KEYS)) {
        problems.push(`${pwhere} keys must be exactly ${PLAYER_KEYS.join(", ")}`);
        return;
      }
      const pid = player.player_id;
      if (typeof pid !== "string" || !PLAYER_ID.test(pid) || !pid.startsWith(`${cid}P`)) {
        problems.push(`${pwhere}.player_id must start with its club id and look like C1P01`);
      } else if (playerIds.has(pid)) {
        problems.push(`${pwhere}.player_id ${pid} is listed twice`);
      } else {
        playerIds.add(pid);
      }
      if (!nonEmptyString(player.name)) {
        problems.push(`${pwhere}.name must be a non-empty string`);
      }
      const shirt = player.shirt;
      if (!isInt(shirt) || shirt < 1 || shirt > 99) {
        problems.push(`${pwhere}.shirt must be an integer from 1 to 99`);
      } else if (shirts.has(shirt)) {
        problems.push(`${pwhere}.shirt ${shirt} is used twice in one club`);
      } else {
        shirts.add(shirt);
      }
      if (typeof player.position !== "string" || !POSITIONS.has(player.position)) {
        problems.push(`${pwhere}.position must be one of DF, FW, GK, MF`);
      }
    });
  });
  for (const key of ["home", "away"]) {
    const value = world[key];
    if (typeof value !== "string" || !clubIds.has(value)) {
      problems.push(`world.${key} must be one of the listed club ids`);
    }
  }
  if (world.home === world.away) {
    problems.push("world.home and world.away must differ");
  }
  return problems;
}

export function parseManifestText(text: string): DataManifest {
  const manifest: unknown = JSON.parse(text);
  const problems = validateManifest(manifest);
  if (problems.length) {
    throw new Error(problems.join("; "));
  }
  return manifest as DataManifest;
}

export function parseWorldText(text: string): World {
  const world: unknown = JSON.parse(text);
  const problems = validateWorld(world);
  if (problems.length) {
    throw new Error(problems.join("; "));
  }
  return world as World;
}

export function fileUrl(base: string, name: string): string {
  if (!isSafeFileName(name)) {
    throw new Error("refusing an unsafe file name in the manifest");
  }
  return base + name;
}

export function worldUrl(base: string, name: string): string {
  if (!isSafeWorldName(name)) {
    throw new Error("refusing an unsafe world file name in the manifest");
  }
  return base + name;
}

/* An HTML page where JSON or NDJSON was expected is the host's fallback page
   for a missing file, and is treated exactly like a 404. */
export function looksLikeHtml(text: string): boolean {
  return /^\s*</.test(text);
}

/* ------------------------------------------------------------- names */

export interface NameIndex {
  known: boolean;
  league: string;
  club(id: string): string;
  player(id: string | null | undefined): string;
  clubRecord(id: string): Club | null;
}

/* Names for the ids in the event log. Without a world every lookup answers a
   plain role word, never an id, so an id never reaches a sentence on screen. */
export function nameIndex(world: World | null, homeId: string): NameIndex {
  const clubs = new Map<string, Club>();
  const players = new Map<string, Player>();
  if (world) {
    for (const club of world.clubs) {
      clubs.set(club.club_id, club);
      for (const player of club.players) {
        players.set(player.player_id, player);
      }
    }
  }
  return {
    known: Boolean(world),
    league: world ? world.league : "",
    club(id) {
      const record = clubs.get(id);
      if (record) {
        return record.name;
      }
      return id === homeId ? "Home club" : "Away club";
    },
    player(id) {
      if (!id) {
        return "";
      }
      const record = players.get(id);
      return record ? `${record.name} (${record.shirt})` : "a player";
    },
    clubRecord(id) {
      return clubs.get(id) ?? null;
    },
  };
}

/* Ids used in the events that the world does not name. */
export function unknownIds(events: MatchEvent[], world: World | null): string[] {
  const index = nameIndex(world, "");
  const missing = new Set<string>();
  for (const e of events) {
    if (!index.clubRecord(e.team)) {
      missing.add(e.team);
    }
    for (const pid of [e.player, e.related_player]) {
      if (pid && index.player(pid) === "a player") {
        missing.add(pid);
      }
    }
  }
  return [...missing].sort();
}

/* ------------------------------------------------------------- events */

export function parseNdjson(text: string): MatchEvent[] {
  const events: MatchEvent[] = [];
  text.split("\n").forEach((raw, i) => {
    const line = raw.replace(/\r$/, "");
    if (!line) {
      return;
    }
    const event: unknown = JSON.parse(line);
    if (
      !isObject(event) ||
      !isInt(event.seq) ||
      !isInt(event.period) ||
      !isInt(event.minute) ||
      !isInt(event.second) ||
      typeof event.type !== "string" ||
      typeof event.team !== "string" ||
      !isInt(event.x_dm) ||
      !isInt(event.y_dm)
    ) {
      throw new Error(`line ${i + 1} is not an event of the published schema`);
    }
    events.push(event as unknown as MatchEvent);
  });
  if (!events.length) {
    throw new Error("the file holds no events");
  }
  return events.sort((a, b) => a.seq - b.seq);
}

export function countLines(text: string): number {
  return text.split("\n").filter((line) => line.replace(/\r$/, "") !== "").length;
}

/* The generator gives the first-half kick-off to the home club, so the team of
   the first event is the home club (web/data-fixture/README.txt, rule 2). */
export function homeTeam(events: MatchEvent[]): string {
  return events[0]?.team ?? "";
}

export function awayTeam(events: MatchEvent[], home: string): string {
  return events.find((e) => e.team !== home)?.team ?? "";
}

export type FramePoint = [number, number, number | null, number | null];

/* to_fixed_frame: the home club attacks towards x = 1050 in both halves; every
   other event is rotated 180 degrees on both axes, end point included. */
export function toFixedFrame(event: MatchEvent, homeId: string): FramePoint {
  const ex = event.end_x_dm ?? null;
  const ey = event.end_y_dm ?? null;
  if (event.team === homeId) {
    return [event.x_dm, event.y_dm, ex, ey];
  }
  if (ex === null || ey === null) {
    return [PITCH_X_DM - event.x_dm, PITCH_Y_DM - event.y_dm, null, null];
  }
  return [PITCH_X_DM - event.x_dm, PITCH_Y_DM - event.y_dm, PITCH_X_DM - ex, PITCH_Y_DM - ey];
}

/* ------------------------------------------------------------- clock */

function clockOf(event: MatchEvent): number {
  return event.minute * 60 + event.second;
}

export interface Timeline {
  times: number[];
  p1End: number;
  hasSecondHalf: boolean;
  total: number;
}

/* A replay axis in seconds that never runs backwards: the second half follows
   straight after the last first-half event, since its clock restarts at 45:00
   while first-half stoppage also ran past 45:00. */
export function buildTimeline(events: MatchEvent[]): Timeline {
  let p1End = 0;
  let hasSecondHalf = false;
  for (const e of events) {
    if (e.period === 1) {
      p1End = Math.max(p1End, clockOf(e) + 1);
    } else {
      hasSecondHalf = true;
    }
  }
  const times = events.map((e) => (e.period === 1 ? clockOf(e) : p1End + Math.max(0, clockOf(e) - HALF_SECONDS)));
  const last = times.length ? times[times.length - 1] : 0;
  return { times, p1End, hasSecondHalf, total: last + 1 };
}

export function matchClockAt(t: number, timeline: Timeline): { period: number; clock: number } {
  if (t < timeline.p1End || !timeline.hasSecondHalf) {
    return { period: 1, clock: Math.max(0, Math.floor(t)) };
  }
  return { period: 2, clock: HALF_SECONDS + Math.floor(t - timeline.p1End) };
}

/* Football minutes: 0:00 to 0:59 is 1'. Stoppage shows as 45+n or 90+n. */
export function formatMinute(period: number, clock: number): string {
  const minute = Math.floor(clock / 60) + 1;
  if (period === 1 && minute > 45) {
    return `45+${minute - 45}'`;
  }
  if (period === 2 && minute > 90) {
    return `90+${minute - 90}'`;
  }
  return `${minute}'`;
}

export function eventMinute(event: MatchEvent): string {
  return formatMinute(event.period, clockOf(event));
}

/* Index of the last event at or before replay time t, or -1. */
export function indexAt(times: number[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/* ------------------------------------------------------------- derived */

export function isGoal(event: MatchEvent): boolean {
  return event.type === "shot" && event.outcome === "goal";
}

export interface RunningScore {
  home: number[];
  away: number[];
}

export function runningScore(events: MatchEvent[], home: string): RunningScore {
  const out: RunningScore = { home: [], away: [] };
  let h = 0;
  let a = 0;
  for (const e of events) {
    if (isGoal(e)) {
      if (e.team === home) {
        h += 1;
      } else {
        a += 1;
      }
    }
    out.home.push(h);
    out.away.push(a);
  }
  return out;
}

export function scoreAt(score: RunningScore, index: number): { home: number; away: number } {
  if (index < 0) {
    return { home: 0, away: 0 };
  }
  return { home: score.home[index], away: score.away[index] };
}

const SHOT_WORDS: Record<string, string> = { goal: "Goal", saved: "Shot saved", off_target: "Shot off target", blocked: "Shot blocked" };
const CARD_WORDS: Record<string, string> = { yellow: "Yellow card", second_yellow: "Second yellow card", red: "Red card" };

export type MomentKind = "goal" | "shot" | "card" | "sub";

export interface Moment {
  kind: MomentKind;
  index: number;
  time: number;
  minute: string;
  text: string;
  ids: string;
}

/* The raw ids of one event, for the details row under a caption. */
export function eventIds(e: MatchEvent): string {
  const parts = [`event ${e.event_id || `seq ${e.seq}`}`, `team ${e.team}`];
  if (e.player) {
    parts.push(`player ${e.player}`);
  }
  if (e.related_player) {
    parts.push(`related player ${e.related_player}`);
  }
  return parts.join(", ");
}

/* Goals, shots, cards and substitutions, in match order. */
export function keyMoments(events: MatchEvent[], timeline: Timeline, names?: NameIndex): Moment[] {
  const n = names ?? nameIndex(null, homeTeam(events));
  const out: Moment[] = [];
  events.forEach((e, i) => {
    let kind: MomentKind | null = null;
    let text = "";
    const who = e.player ? `, ${n.player(e.player)}` : "";
    if (e.type === "shot") {
      kind = isGoal(e) ? "goal" : "shot";
      text = `${SHOT_WORDS[e.outcome ?? ""] ?? "Shot"}, ${n.club(e.team)}${who}`;
    } else if (e.type === "card") {
      kind = "card";
      text = `${CARD_WORDS[e.outcome ?? ""] ?? "Card"}, ${n.club(e.team)}${who}`;
    } else if (e.type === "sub") {
      kind = "sub";
      text = `Substitution, ${n.club(e.team)}: ${n.player(e.related_player)} on for ${n.player(e.player)}`;
    }
    if (kind) {
      out.push({ kind, index: i, time: timeline.times[i], minute: eventMinute(e), text, ids: eventIds(e) });
    }
  });
  return out;
}

export interface PressureSide {
  under: number;
  total: number;
}

/* Share of on-ball actions made under pressure, per team, over the last five
   minutes of replay time up to index. */
export function pressureWindow(
  events: MatchEvent[],
  timeline: Timeline,
  index: number,
  home: string,
): { home: PressureSide; away: PressureSide } {
  const result = { home: { under: 0, total: 0 }, away: { under: 0, total: 0 } };
  if (index < 0) {
    return result;
  }
  const from = timeline.times[index] - PRESSURE_WINDOW_SECONDS;
  for (let i = index; i >= 0 && timeline.times[i] >= from; i -= 1) {
    const e = events[i];
    if (!ON_BALL.has(e.type)) {
      continue;
    }
    const side = e.team === home ? result.home : result.away;
    side.total += 1;
    if (e.under_pressure === true) {
      side.under += 1;
    }
  }
  return result;
}

export function percent(part: PressureSide): string {
  return part.total ? `${Math.round((100 * part.under) / part.total)}%` : "no actions";
}

/* Ball position in fixed-frame decimetres at replay time t. With tween on, a
   pass, carry or shot travels from its start to its end until the next event;
   with tween off (reduced motion) the ball jumps event by event. */
export function ballAt(
  events: MatchEvent[],
  timeline: Timeline,
  t: number,
  home: string,
  tween: boolean,
): { x: number; y: number; index: number } {
  const i = indexAt(timeline.times, t);
  if (i < 0) {
    return { x: PITCH_X_DM / 2, y: PITCH_Y_DM / 2, index: -1 };
  }
  const p = toFixedFrame(events[i], home);
  if (!tween || p[2] === null || p[3] === null) {
    return { x: p[0], y: p[1], index: i };
  }
  const start = timeline.times[i];
  const next = i + 1 < timeline.times.length ? timeline.times[i + 1] : start + 1;
  const span = Math.max(1, next - start);
  const f = Math.min(1, Math.max(0, (t - start) / span));
  return { x: p[0] + (p[2] - p[0]) * f, y: p[1] + (p[3] - p[1]) * f, index: i };
}

export function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function entryForSeed(manifest: DataManifest, seed: number): ManifestEntry | null {
  return manifest.files.find((f) => f.seed === seed) ?? null;
}
