/*
  The match data rules, held to the same case lists the Python validators and
  the vanilla web/agon-core.js are held to (tests/js/*.json at the repository
  root), and to the fixed-frame golden that tests/test_web_data.py holds to
  agon.synth.schema.to_fixed_frame.
*/
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ballAt,
  buildTimeline,
  formatMinute,
  homeTeam,
  isSafeFileName,
  isSafeWorldName,
  keyMoments,
  looksLikeHtml,
  nameIndex,
  parseNdjson,
  parseWorldText,
  pressureWindow,
  runningScore,
  toFixedFrame,
  unknownIds,
  validateManifest,
  validateWorld,
} from "../src/lib/core";
import { resolveManifest } from "../src/lib/loader";
import type { MatchEvent } from "../src/lib/types";
import { CASES, REPO, WEB, diskFetch, readJson } from "./helpers";

type Case = { case: string; valid: boolean; problem?: string } & Record<string, unknown>;

function ev(fields: Partial<MatchEvent>): MatchEvent {
  return {
    seq: 0,
    period: 1,
    minute: 0,
    second: 0,
    type: "pass",
    team: "H",
    player: "HP01",
    x_dm: 100,
    y_dm: 100,
    end_x_dm: null,
    end_y_dm: null,
    under_pressure: false,
    outcome: "complete",
    qualifier: null,
    related_player: null,
    ...fields,
  };
}

describe("shared case lists", () => {
  it("manifest rules agree with the Python validator", () => {
    const cases = readJson<Case[]>(join(CASES, "manifest_cases.json"));
    expect(cases.length).toBeGreaterThanOrEqual(12);
    for (const c of cases) {
      const problems = validateManifest(c.manifest);
      expect(problems.length === 0, `${c.case}: ${problems.join("; ")}`).toBe(c.valid);
      if (c.problem !== undefined) {
        expect(problems.some((p) => p.includes(c.problem as string)), `${c.case}: ${problems.join("; ")}`).toBe(true);
      }
    }
  });

  it("world rules agree with the Python validator", () => {
    const cases = readJson<Case[]>(join(CASES, "world_cases.json"));
    expect(cases.length).toBeGreaterThanOrEqual(16);
    for (const c of cases) {
      const problems = validateWorld(c.world);
      expect(problems.length === 0, `${c.case}: ${problems.join("; ")}`).toBe(c.valid);
      if (c.problem !== undefined) {
        expect(problems.some((p) => p.includes(c.problem as string)), `${c.case}: ${problems.join("; ")}`).toBe(true);
      }
    }
  });

  it("toFixedFrame agrees with Python on every fixture event", () => {
    const golden = readJson<{ source: string; home: string; points: unknown[] }>(join(CASES, "fixed_frame_golden.json"));
    const events = parseNdjson(readFileSync(join(REPO, golden.source), "utf8"));
    expect(homeTeam(events)).toBe(golden.home);
    expect(events.length).toBe(golden.points.length);
    let rotated = 0;
    events.forEach((e, i) => {
      expect(toFixedFrame(e, golden.home), `event ${e.event_id}`).toEqual(golden.points[i]);
      if (e.team !== golden.home) {
        rotated += 1;
      }
    });
    expect(rotated).toBeGreaterThan(0);
  });
});

describe("frame, clock and derived values", () => {
  it("rotates both axes for the away side, never by period", () => {
    expect(toFixedFrame(ev({ team: "A", x_dm: 100, y_dm: 50, end_x_dm: 300, end_y_dm: 600 }), "H")).toEqual([950, 630, 750, 80]);
    expect(toFixedFrame(ev({ team: "A", period: 2, x_dm: 100, y_dm: 50 }), "H")).toEqual([950, 630, null, null]);
    expect(toFixedFrame(ev({ team: "H", period: 2, x_dm: 100, y_dm: 50 }), "H")).toEqual([100, 50, null, null]);
  });

  it("uses football minutes and stoppage notation", () => {
    expect(formatMinute(1, 0)).toBe("1'");
    expect(formatMinute(1, 59)).toBe("1'");
    expect(formatMinute(1, 60)).toBe("2'");
    expect(formatMinute(1, 2700)).toBe("45+1'");
    expect(formatMinute(2, 2700)).toBe("46'");
    expect(formatMinute(2, 5400)).toBe("90+1'");
  });

  it("never runs the replay axis backwards across half time", () => {
    const events = [
      ev({ seq: 0, period: 1, minute: 0, second: 0 }),
      ev({ seq: 1, period: 1, minute: 46, second: 30 }),
      ev({ seq: 2, period: 2, minute: 45, second: 0 }),
      ev({ seq: 3, period: 2, minute: 50, second: 0 }),
    ];
    const timeline = buildTimeline(events);
    for (let i = 1; i < timeline.times.length; i += 1) {
      expect(timeline.times[i]).toBeGreaterThanOrEqual(timeline.times[i - 1]);
    }
    expect(timeline.hasSecondHalf).toBe(true);
  });

  it("counts the score, the key moments and the pressure window from the events", () => {
    const events = [
      ev({ seq: 0, team: "H" }),
      ev({ seq: 1, team: "H", type: "shot", outcome: "goal", minute: 3, player: "HP09" }),
      ev({ seq: 2, team: "A", type: "card", outcome: "yellow", minute: 4, player: "AP04" }),
      ev({ seq: 3, team: "A", type: "pass", minute: 5, under_pressure: true }),
    ];
    const timeline = buildTimeline(events);
    const score = runningScore(events, "H");
    expect(score.home[3]).toBe(1);
    expect(score.away[3]).toBe(0);
    const moments = keyMoments(events, timeline, nameIndex(null, "H"));
    expect(moments.map((m) => m.kind)).toEqual(["goal", "card"]);
    expect(moments[0].text).toBe("Goal, Home club, a player");
    const press = pressureWindow(events, timeline, 3, "H");
    expect(press.away).toEqual({ under: 1, total: 1 });
  });

  it("glides the ball along a pass only when motion is allowed", () => {
    const events = [ev({ seq: 0, x_dm: 100, y_dm: 100, end_x_dm: 300, end_y_dm: 100 }), ev({ seq: 1, minute: 0, second: 10, x_dm: 300, y_dm: 100 })];
    const timeline = buildTimeline(events);
    expect(ballAt(events, timeline, 5, "H", true).x).toBe(200);
    expect(ballAt(events, timeline, 5, "H", false).x).toBe(100);
  });
});

describe("files and names", () => {
  it("refuses unsafe file names and treats an HTML page as missing", () => {
    expect(isSafeFileName("1.ndjson")).toBe(true);
    expect(isSafeFileName("../1.ndjson")).toBe(false);
    expect(isSafeWorldName("1.world.json")).toBe(true);
    expect(isSafeWorldName("https://x/1.world.json")).toBe(false);
    expect(looksLikeHtml("  <!doctype html>")).toBe(true);
    expect(() => parseNdjson("<html></html>")).toThrow();
  });

  it("names every id in the fixture events from the fixture world, and both hash to the manifest", () => {
    const manifest = readJson<{ files: { name: string; sha256: string; world: string; world_sha256: string }[] }>(
      join(WEB, "data-fixture", "manifest.json"),
    );
    const entry = manifest.files[0];
    const eventsBytes = readFileSync(join(WEB, "data-fixture", entry.name));
    const worldBytes = readFileSync(join(WEB, "data-fixture", entry.world));
    expect(createHash("sha256").update(eventsBytes).digest("hex")).toBe(entry.sha256);
    expect(createHash("sha256").update(worldBytes).digest("hex")).toBe(entry.world_sha256);
    const events = parseNdjson(eventsBytes.toString("utf8"));
    const world = parseWorldText(worldBytes.toString("utf8"));
    expect(unknownIds(events, world)).toEqual([]);
    const names = nameIndex(world, homeTeam(events));
    expect(names.club(homeTeam(events))).toBe("Pellmoor Harriers");
  });

  it("falls back to the committed fixture when data/ is absent, and says why when data/ is broken", async () => {
    const fromFixture = await resolveManifest(diskFetch);
    expect(fromFixture.source).toBe("fixture");
    expect(fromFixture.notice).toBeNull();
    const broken = await resolveManifest(async (url) =>
      url === "data/manifest.json" ? { ok: true, status: 200, text: '{"generator":"x","files":[]}' } : diskFetch(url),
    );
    expect(broken.source).toBe("fixture");
    expect(broken.notice).toContain("could not be used");
    await expect(resolveManifest(async () => ({ ok: false, status: 404, text: "" }))).rejects.toThrow("No match data could be loaded");
  });
});
