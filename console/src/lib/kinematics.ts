/*
  Synthetic tracking (kinematics/<seed>.kinematics.json, agon.webkinematics/1,
  written at deploy by agon.webkinematics from the exact event log the site
  serves). Positions for all 22 players at 1 Hz, modelled from the event log
  by agon.synth.kinematics within speed and acceleration caps: SYNTHETIC, not
  measured. Each file is 1.26 to 1.29 MB (curl size_download on the live site, 2026-10-20), so it is never
  part of the prerendered page: the Match Centre fetches it once the match is ready on a screen 1024 px
  wide or wider, and on a narrower screen only when the viewer asks for the players on the pitch.

  The browser reads positions, triggers and totals and draws them; it computes
  no speed or distance of its own.
*/
import { isObject } from "./core";
import { fetchBytes, sha256Hex } from "./hash";

export const KINEMATICS_MANIFEST = "kinematics/manifest.json";
export const KINEMATICS_SCHEMA = "agon.webkinematics/1";

export interface Track {
  player_id: string;
  team: string;
  start_s: number;
  xy: number[];
}

export interface Period {
  period: number;
  start_s: number;
  end_s: number;
  ball: number[];
  players: Track[];
}

export interface KinPlayer {
  player_id: string;
  team: string;
  distance_dm: number;
  top_speed_kmh_x10: number;
  sprints: { period: number; start_s: number; end_s: number }[];
  top_speed_triggers: { period: number; t_s: number }[];
  distance_milestones: { km: number; period: number; t_s: number }[];
}

export interface KinematicsFile {
  schema: string;
  seed: number;
  home: string;
  away: string;
  events_sha256: string;
  note: string;
  periods: Period[];
  players: KinPlayer[];
}

export type KinematicsLoad =
  | { status: "off" }
  | { status: "loading" }
  | { status: "missing"; message: string }
  | { status: "error"; message: string }
  | { status: "ready"; file: KinematicsFile };

export function validateKinematics(body: unknown, seed: number, eventsSha256: string): string[] {
  if (!isObject(body) || body.schema !== KINEMATICS_SCHEMA) {
    return [`the file is not ${KINEMATICS_SCHEMA}`];
  }
  const problems: string[] = [];
  if (body.seed !== seed) {
    problems.push(`the file is for seed ${String(body.seed)}`);
  }
  if (body.events_sha256 !== eventsSha256) {
    problems.push("the file was derived from another event log than the replay");
  }
  if (!Array.isArray(body.periods) || !Array.isArray(body.players)) {
    problems.push("periods and players must be lists");
  }
  return problems;
}

export async function loadKinematics(seed: number, eventsSha256: string): Promise<KinematicsLoad> {
  try {
    const list: unknown = JSON.parse((await fetchBytes(KINEMATICS_MANIFEST)).text);
    if (!isObject(list) || list.schema !== KINEMATICS_SCHEMA || !Array.isArray(list.files)) {
      return { status: "error", message: "kinematics/manifest.json is not an agon.webkinematics/1 list." };
    }
    const entry = (list.files as unknown[]).find((f) => isObject(f) && f.seed === seed);
    if (!isObject(entry) || typeof entry.name !== "string" || typeof entry.sha256 !== "string" || !/^[0-9]+\.kinematics\.json$/.test(entry.name)) {
      return { status: "missing", message: `There is no synthetic tracking file for seed ${seed}.` };
    }
    if (entry.events_sha256 !== eventsSha256) {
      return { status: "missing", message: "The synthetic tracking was derived from another event log than this replay, so it is not drawn." };
    }
    const fetched = await fetchBytes(`kinematics/${entry.name}`);
    if ((await sha256Hex(fetched.buffer)) !== entry.sha256) {
      return { status: "error", message: `kinematics/${entry.name} does not hash to its list entry, so it is not drawn.` };
    }
    const body: unknown = JSON.parse(fetched.text);
    const problems = validateKinematics(body, seed, eventsSha256);
    if (problems.length) {
      return { status: "error", message: `kinematics/${entry.name} is not drawn: ${problems.join("; ")}.` };
    }
    return { status: "ready", file: body as KinematicsFile };
  } catch (error) {
    const text = error instanceof Error ? error.message : String(error);
    if (/answered 404|returned a web page/.test(text)) {
      return { status: "missing", message: "This build has no synthetic tracking files." };
    }
    return { status: "error", message: `The synthetic tracking could not be read: ${text}.` };
  }
}

export interface PlayerAt {
  player_id: string;
  team: string;
  x: number;
  y: number;
}

/* Every player's position (decimetres, fixed frame) at match clock second `clock` of `period`. */
export function playersAt(file: KinematicsFile, period: number, clock: number): PlayerAt[] {
  const per = file.periods.find((p) => p.period === period);
  if (!per) {
    return [];
  }
  const out: PlayerAt[] = [];
  for (const track of per.players) {
    const k = Math.floor(clock) - track.start_s;
    if (k >= 0 && 2 * k + 1 < track.xy.length) {
      out.push({ player_id: track.player_id, team: track.team, x: track.xy[2 * k], y: track.xy[2 * k + 1] });
    }
  }
  return out;
}

export type Indicator = { kind: "sprint" } | { kind: "top_speed" } | { kind: "milestone"; km: number };

/* The trigger indicators that apply to a player at a moment: inside a sprint, a top-speed step in
   the last 3 seconds, or a 2 km milestone in the last 5 seconds. */
export function indicatorsAt(player: KinPlayer, period: number, clock: number): Indicator[] {
  const s = Math.floor(clock);
  const out: Indicator[] = [];
  if (player.sprints.some((r) => r.period === period && r.start_s <= s && s <= r.end_s)) {
    out.push({ kind: "sprint" });
  }
  if (player.top_speed_triggers.some((t) => t.period === period && t.t_s <= s && s - t.t_s < 3)) {
    out.push({ kind: "top_speed" });
  }
  const milestone = player.distance_milestones.find((m) => m.period === period && m.t_s <= s && s - m.t_s < 5);
  if (milestone) {
    out.push({ kind: "milestone", km: milestone.km });
  }
  return out;
}

export function indicatorWords(i: Indicator): string {
  if (i.kind === "sprint") {
    return "sprinting (25.2 km/h or more)";
  }
  if (i.kind === "top_speed") {
    return "top-speed burst (30 km/h or more)";
  }
  return `${i.km} km covered`;
}

export function runningKmh(x10: number): string {
  return `${(x10 / 10).toFixed(1)} km/h`;
}

export function km(dm: number): string {
  return `${(dm / 10000).toFixed(1)} km`;
}
