/*
  The match analysis as the browser reads it: the committed fixture
  tests/fixtures/match/1.json is agon.webmatch's output for seed 1, held byte
  for byte by tests/test_webmatch.py. The validator accepts it and refuses a
  broken copy, the pickers read the right point, and every view of the
  analysis (fan, analyst, one metric, following a player, a favourite club)
  renders without an inline style, with only the values the file holds.
*/
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Analysis } from "../src/components/match/analysis";
import { MatchTimeline } from "../src/components/match/timeline";
import { Scoreboard } from "../src/components/match/scoreboard";
import { DEFAULT_PREFS, METRICS, type Preferences } from "../src/hooks/preferences";
import { cueAt, cueText, validateCues, type Cue } from "../src/lib/cues";
import { axisSeconds, liveAt, percentOfPermille, statValue, validateMatchFile, windowWords, xg, type MatchFile } from "../src/lib/match";
import { REPO } from "./helpers";

const FIXTURE = join(REPO, "tests", "fixtures", "match", "1.json");

function fixture(): MatchFile {
  return JSON.parse(readFileSync(FIXTURE, "utf8")) as MatchFile;
}

const NAMES = { home: "Pellmoor Harriers", away: "Zennmark Town" };

describe("the match analysis file", () => {
  it("passes the browser's shape check", () => {
    expect(validateMatchFile(fixture())).toEqual([]);
  });

  it("is refused when a key, a side, a kind or a probability is wrong", () => {
    const missing = fixture() as unknown as Record<string, unknown>;
    delete missing.shots;
    expect(validateMatchFile(missing)[0]).toContain("keys differ");
    const side = fixture();
    (side.goals[0] as unknown as Record<string, unknown>).side = "neutral";
    expect(validateMatchFile(side).join(" ")).toContain("side");
    const kind = fixture();
    (kind.timeline[0] as unknown as Record<string, unknown>).kind = "offside";
    expect(validateMatchFile(kind).join(" ")).toContain("known kind");
    const wp = fixture();
    wp.win_probability[3].home_ppm += 1;
    expect(validateMatchFile(wp).join(" ")).toContain("1000000");
  });

  it("picks the last per-minute point at or before the replay's event", () => {
    const file = fixture();
    expect(liveAt(file, null)).toBeNull();
    const point = liveAt(file, file.goals[0].seq);
    expect(point).not.toBeNull();
    expect(point!.seq).toBeLessThanOrEqual(file.goals[0].seq);
    expect(liveAt(file, file.last_seq)).toEqual(file.live[file.live.length - 1]);
  });

  it("puts a second-half event after half time on the drawn axis", () => {
    const file = fixture();
    expect(axisSeconds(file, 2, 45, 0)).toBe(2700 + file.axis.half_offset_s);
    expect(axisSeconds(file, 2, 45, 0)).toBeGreaterThanOrEqual(file.axis.half_time_s);
  });

  it("formats values without computing new ones", () => {
    expect(xg(971747)).toBe("0.97");
    expect(percentOfPermille(543)).toBe("54%");
    expect(windowWords("p2 60-65")).toBe("2nd half, minutes 60 to 65");
    const ppda = fixture().team_stats.find((s) => s.key === "ppda")!;
    expect(statValue(ppda, "home")).toBe((ppda.home! / 100).toFixed(2));
  });
});

/* React marks the seam between two adjacent text nodes with an empty comment. */
function clean(html: string): string {
  return html.replace(/<!-- -->/g, "");
}

function prefs(change: Partial<Preferences>): Preferences {
  return { ...DEFAULT_PREFS, ...change };
}

function render(change: Partial<Preferences>, nowSeq: number | null = null): string {
  const file = fixture();
  const seq = nowSeq ?? file.last_seq;
  const nowS = file.axis.end_s;
  return clean(renderToString(<Analysis file={file} names={NAMES} prefs={prefs(change)} nowS={nowS} nowSeq={seq} />));
}

describe("every view of the analysis renders", () => {
  it("fan mode tells the story in words and keeps to simple stats", () => {
    const html = render({ mode: "fan" });
    expect(html).toContain("The story so far");
    expect(html).toContain("Goal for Pellmoor Harriers (home): Arno Quenrick scores.");
    expect(html).toContain("Show all");
    expect(html).toContain("Star player");
    expect(html).not.toContain("PPDA (lower presses harder)");
    expect(html).not.toContain("Territory");
  });

  it("analyst mode shows every chart and the full scorecard", () => {
    const html = render({ mode: "analyst" });
    for (const title of ["Win probability", "xG race", "Momentum, pressure and control", "Shot map", "Territory", "Team stats", "Player scorecards"]) {
      expect(html).toContain(title);
    }
    expect(html).toContain("PPDA (lower presses harder)");
    expect(html).not.toContain("The story so far");
  });

  it("one metric tells the match through that metric alone", () => {
    for (const metric of METRICS) {
      const html = render({ metric });
      expect(html).toContain("The whole match told through one number");
      expect(html).not.toContain("Player scorecards");
    }
  });

  it("following a player shows their scorecard, pass quality and heat zones", () => {
    const file = fixture();
    const star = file.star!;
    const html = render({ mode: "analyst", player: star.player_id, focus: true });
    expect(html).toContain(`Following ${star.name}`);
    expect(html).toContain("Mean pass difficulty");
    expect(html).toContain(`Heat zones of ${star.name}.`);
  });

  it("a favourite club gets its own view", () => {
    const file = fixture();
    const html = render({ club: file.clubs.home.club_id });
    expect(html).toContain("Your club: Pellmoor Harriers");
  });

  it("never writes an inline style or a dash", () => {
    const dashes = new RegExp(`[${String.fromCharCode(0x2013, 0x2014, 0x2015)}]`);
    for (const html of [render({ mode: "fan" }), render({ mode: "analyst", player: fixture().star!.player_id, focus: true }), render({ metric: "ppda" })]) {
      expect(html).not.toMatch(/\sstyle=/i);
      expect(html).not.toMatch(dashes);
    }
  });

  it("before kick-off the story waits and the scoreboard says so", () => {
    expect(render({ mode: "fan" }, -1)).toContain("Nothing big has happened yet");
    const file = fixture();
    const html = clean(renderToString(
      <Scoreboard clubs={file.clubs} names={NAMES} score={{ home: 0, away: 0 }} minute="1'" half="1st half" live={null} goals={[]} possessionRule={file.rules.possession} />,
    ));
    expect(html).toContain("No goal yet");
    expect(html).toContain("none yet");
  });

  it("the timeline draws home above and away below, and fan mode keeps the big moments", () => {
    const file = fixture();
    const all = clean(renderToString(<MatchTimeline file={file} nowS={file.axis.end_s} names={NAMES} />));
    const fan = clean(renderToString(<MatchTimeline file={file} nowS={file.axis.end_s} names={NAMES} fan />));
    expect(all).toContain("Pellmoor Harriers above the line");
    expect(all).toContain("Foul");
    expect(fan).not.toContain(">Foul<");
    expect(all).not.toMatch(/\sstyle=/i);
  });
});

describe("live cues", () => {
  const cue: Cue = { at_s: 100, until_s: 108, kind: "goal", text: { en: "Goal", el: "Γκολ" } };

  it("are drawn only from a valid file under an approved charter", () => {
    const good = { schema: "agon.cues/1", seed: 1, charter_sha256: "a".repeat(64), cues: [cue] };
    expect(validateCues(good, 1)).toEqual([]);
    expect(validateCues({ ...good, charter_sha256: "nope" }, 1).join(" ")).toContain("charter");
    expect(validateCues({ ...good, seed: 2 }, 1).join(" ")).toContain("seed");
  });

  it("show the cue in force at the replay time, in the chosen language when it exists", () => {
    expect(cueAt([cue], 99)).toBeNull();
    expect(cueAt([cue], 104)).toEqual(cue);
    expect(cueAt([cue], 108)).toBeNull();
    expect(cueText(cue, "el")).toEqual({ text: "Γκολ", language: "el" });
    expect(cueText(cue, "fr")).toEqual({ text: "Goal", language: "en" });
  });
});
