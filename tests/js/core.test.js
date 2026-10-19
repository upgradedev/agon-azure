/*
  Tests for web/agon-core.js, run with: node --test tests/js/

  No packages: node's built-in test runner, assert and WebCrypto only. The
  frame conversion is held to tests/js/fixed_frame_golden.json, which
  tests/test_web_data.py holds to Python's to_fixed_frame, and the manifest
  rules are held to the same case list the Python validator is held to.
*/
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const core = require(path.join(ROOT, "web", "agon-core.js"));
const FIXTURE = path.join(ROOT, "web", "data-fixture");

function readFixture(name) {
  return fs.readFileSync(path.join(FIXTURE, name));
}

function ev(fields) {
  return Object.assign(
    {
      seq: 0, period: 1, minute: 0, second: 0, type: "pass", team: "H", player: "HP01",
      x_dm: 100, y_dm: 100, end_x_dm: null, end_y_dm: null, under_pressure: false,
      outcome: "complete", qualifier: null, related_player: null
    },
    fields
  );
}

test("toFixedFrame agrees with Python on every fixture event", () => {
  const golden = JSON.parse(fs.readFileSync(path.join(__dirname, "fixed_frame_golden.json"), "utf8"));
  const events = core.parseNdjson(fs.readFileSync(path.join(ROOT, golden.source), "utf8"));
  assert.equal(core.homeTeam(events), golden.home);
  assert.equal(events.length, golden.points.length);
  let rotated = 0;
  events.forEach((e, i) => {
    assert.deepEqual(core.toFixedFrame(e, golden.home), golden.points[i], "event " + e.event_id);
    if (e.team !== golden.home) {
      rotated += 1;
    }
  });
  assert.ok(rotated > 0, "the fixture must contain away events");
});

test("toFixedFrame rotates both axes for the away side, never by period", () => {
  assert.deepEqual(core.toFixedFrame(ev({ team: "A", x_dm: 100, y_dm: 50, end_x_dm: 300, end_y_dm: 600 }), "H"), [950, 630, 750, 80]);
  assert.deepEqual(core.toFixedFrame(ev({ team: "A", period: 2, x_dm: 100, y_dm: 50 }), "H"), [950, 630, null, null]);
  assert.deepEqual(core.toFixedFrame(ev({ team: "H", period: 2, x_dm: 100, y_dm: 50 }), "H"), [100, 50, null, null]);
});

test("manifest rules agree with the Python validator on the shared cases", () => {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "manifest_cases.json"), "utf8"));
  assert.ok(cases.length >= 12);
  for (const c of cases) {
    const problems = core.validateManifest(c.manifest);
    assert.equal(problems.length === 0, c.valid, c.case + ": " + problems.join("; "));
    if (c.problem !== undefined) {
      assert.ok(problems.some((p) => p.indexOf(c.problem) !== -1), c.case + ": " + problems.join("; "));
    }
  }
});

test("the fixture loads, and WebCrypto recomputes its listed hash", async () => {
  const manifest = JSON.parse(readFixture("manifest.json").toString("ascii"));
  assert.deepEqual(core.validateManifest(manifest), []);
  for (const entry of manifest.files) {
    const bytes = readFixture(entry.name);
    const events = core.parseNdjson(bytes.toString("utf8"));
    assert.equal(events.length, entry.events);
    assert.equal(core.countLines(bytes.toString("utf8")), entry.events);
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    assert.equal(core.toHex(digest), entry.sha256);
  }
});

test("a flipped byte changes the recomputed hash", async () => {
  const manifest = JSON.parse(readFixture("manifest.json").toString("ascii"));
  const bytes = Buffer.from(readFixture(manifest.files[0].name));
  bytes[10] = bytes[10] === 0x5a ? 0x59 : 0x5a;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  assert.notEqual(core.toHex(digest), manifest.files[0].sha256);
});

test("formatMinute uses football minutes and stoppage notation", () => {
  assert.equal(core.formatMinute(1, 0), "1'");
  assert.equal(core.formatMinute(1, 2699), "45'");
  assert.equal(core.formatMinute(1, 2700), "45+1'");
  assert.equal(core.formatMinute(1, 2821), "45+3'");
  assert.equal(core.formatMinute(2, 2700), "46'");
  assert.equal(core.formatMinute(2, 5399), "90'");
  assert.equal(core.formatMinute(2, 5400), "90+1'");
});

test("the replay axis never runs backwards across half time", () => {
  const events = [
    ev({ seq: 0, minute: 0, second: 0 }),
    ev({ seq: 1, minute: 47, second: 1 }),
    ev({ seq: 2, period: 2, minute: 45, second: 0 }),
    ev({ seq: 3, period: 2, minute: 46, second: 0 })
  ];
  const tl = core.buildTimeline(events);
  assert.deepEqual(tl.times, [0, 2821, 2822, 2882]);
  assert.equal(tl.total, 2883);
  assert.deepEqual(core.matchClockAt(2821, tl), { period: 1, clock: 2821 });
  assert.deepEqual(core.matchClockAt(2822, tl), { period: 2, clock: 2700 });
  assert.equal(core.indexAt(tl.times, -1), -1);
  assert.equal(core.indexAt(tl.times, 2821.5), 1);
  assert.equal(core.indexAt(tl.times, 99999), 3);
});

test("a first-half-only file stays in the first half", () => {
  const tl = core.buildTimeline([ev({ seq: 0 }), ev({ seq: 1, minute: 9, second: 59 })]);
  assert.equal(tl.hasSecondHalf, false);
  assert.deepEqual(core.matchClockAt(599, tl), { period: 1, clock: 599 });
});

test("score, key moments and the pressure window come from the events", () => {
  const events = [
    ev({ seq: 0, type: "possession_change", outcome: "kick_off", player: null }),
    ev({ seq: 1, minute: 3, type: "shot", outcome: "off_target", team: "A", player: "AP09", under_pressure: true }),
    ev({ seq: 2, minute: 4, type: "shot", outcome: "goal", team: "H", player: "HP10" }),
    ev({ seq: 3, minute: 5, type: "card", outcome: "yellow", team: "A", player: "AP04" }),
    ev({ seq: 4, minute: 6, type: "sub", outcome: "on", team: "H", player: "HP07", related_player: "HP15" }),
    ev({ seq: 5, minute: 7, type: "shot", outcome: "goal", team: "A", player: "AP09" })
  ];
  const home = core.homeTeam(events);
  assert.equal(home, "H");
  assert.equal(core.awayTeam(events, home), "A");
  const tl = core.buildTimeline(events);
  const score = core.runningScore(events, home);
  assert.deepEqual(core.scoreAt(score, -1), { home: 0, away: 0 });
  assert.deepEqual(core.scoreAt(score, 2), { home: 1, away: 0 });
  assert.deepEqual(core.scoreAt(score, 5), { home: 1, away: 1 });

  /* Without a world file a sentence never shows an id. */
  const moments = core.keyMoments(events, tl);
  assert.deepEqual(moments.map((m) => m.kind), ["shot", "goal", "card", "sub", "goal"]);
  assert.equal(moments[1].minute, "5'");
  assert.equal(moments[2].text, "Yellow card, Away club, a player");
  assert.equal(moments[3].text, "Substitution, Home club: a player on for a player");
  assert.equal(moments[2].ids, "event seq 3, team A, player AP04");

  /* With one, the names are used and the ids stay in the details row. */
  const world = {
    generator: "synth-v0.2", seed: 1, match_id: "s1-m0", league: "Test League", home: "C1", away: "C2",
    clubs: [
      { club_id: "C1", name: "Home Rovers", short: "HOM", kit_primary: "teal", kit_primary_hex: "#12808a",
        kit_secondary: "amber", kit_secondary_hex: "#e0a100",
        players: [{ player_id: "C1P07", name: "Ann Holt", shirt: 7, position: "MF" }, { player_id: "C1P15", name: "Ben Ash", shirt: 15, position: "FW" }] },
      { club_id: "C2", name: "Away Town", short: "AWA", kit_primary: "plum", kit_primary_hex: "#6d3a6b",
        kit_secondary: "sky", kit_secondary_hex: "#5aa7de",
        players: [{ player_id: "C2P04", name: "Cal Dunby", shirt: 4, position: "DF" }] }
    ]
  };
  assert.deepEqual(core.validateWorld(world), []);
  const named = events.map((e) => Object.assign({}, e, {
    team: e.team === "H" ? "C1" : "C2",
    player: e.player ? { HP07: "C1P07", HP10: "C1P07", AP04: "C2P04", AP09: "C2P04" }[e.player] : null,
    related_player: e.related_player ? "C1P15" : null
  }));
  const names = core.nameIndex(world, "C1");
  const namedMoments = core.keyMoments(named, core.buildTimeline(named), names);
  assert.equal(namedMoments[2].text, "Yellow card, Away Town, Cal Dunby (4)");
  assert.equal(namedMoments[3].text, "Substitution, Home Rovers: Ben Ash (15) on for Ann Holt (7)");
  assert.equal(namedMoments[3].ids, "event seq 4, team C1, player C1P07, related player C1P15");
  for (const m of namedMoments) {
    assert.doesNotMatch(m.text, /\bC\dP?\d*\b/, "an id leaked into a sentence: " + m.text);
  }
  assert.deepEqual(core.unknownIds(named, world), []);
  assert.deepEqual(core.unknownIds(events, world), ["A", "AP04", "AP09", "H", "HP07", "HP10", "HP15"]);

  const w = core.pressureWindow(events, tl, 5, home);
  assert.deepEqual(w.away, { under: 1, total: 2 });
  assert.deepEqual(w.home, { under: 0, total: 1 });
  assert.equal(core.percent(w.away), "50%");
  assert.equal(core.percent({ under: 0, total: 0 }), "no actions");
});

test("the ball glides along a pass only when motion is allowed", () => {
  const events = [
    ev({ seq: 0, x_dm: 0, y_dm: 0, end_x_dm: 100, end_y_dm: 200 }),
    ev({ seq: 1, second: 10, x_dm: 100, y_dm: 200 })
  ];
  const tl = core.buildTimeline(events);
  assert.deepEqual(core.ballAt(events, tl, 5, "H", true), { x: 50, y: 100, index: 0 });
  assert.deepEqual(core.ballAt(events, tl, 5, "H", false), { x: 0, y: 0, index: 0 });
  assert.deepEqual(core.ballAt(events, tl, -1, "H", true), { x: 525, y: 340, index: -1 });
});

test("unsafe file names never become URLs", () => {
  assert.equal(core.fileUrl("data/", "1.ndjson"), "data/1.ndjson");
  for (const bad of ["../1.ndjson", "a/1.ndjson", "https://x.example/1.ndjson", "1.json", "", null]) {
    assert.throws(() => core.fileUrl("data/", bad), /unsafe/);
  }
});

test("parseNdjson rejects a web page and a line that is not an event", () => {
  assert.equal(core.looksLikeHtml("  <!doctype html>"), true);
  assert.equal(core.looksLikeHtml('{"a":1}'), false);
  assert.throws(() => core.parseNdjson('{"a":1}\n'), /not an event/);
  assert.throws(() => core.parseNdjson("\n\n"), /no events/);
});

/* A fake fetch: url -> {status, body} or an Error to throw. */
function fakeFetch(routes) {
  return (url, options) => {
    assert.equal(options.cache, "no-store");
    const route = routes[url];
    if (route instanceof Error) {
      return Promise.reject(route);
    }
    const r = route || { status: 404, body: "Not found" };
    return Promise.resolve({ status: r.status, ok: r.status >= 200 && r.status < 300, text: () => Promise.resolve(r.body) });
  };
}

const GOOD = JSON.stringify({
  generator: "synth-v0.1",
  files: [{ name: "1.ndjson", sha256: "a".repeat(64), seed: 1, events: 3, world: "1.world.json", world_sha256: "b".repeat(64) }]
});

test("world rules agree with the Python validator on the shared cases", () => {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "world_cases.json"), "utf8"));
  assert.ok(cases.length >= 16);
  for (const c of cases) {
    const problems = core.validateWorld(c.world);
    assert.equal(problems.length === 0, c.valid, c.case + ": " + problems.join("; "));
    if (c.problem !== undefined) {
      assert.ok(problems.some((p) => p.indexOf(c.problem) !== -1), c.case + ": " + problems.join("; "));
    }
  }
});

test("the fixture world loads, names every id in the fixture events and hashes to the manifest", async () => {
  const manifest = JSON.parse(readFixture("manifest.json").toString("ascii"));
  for (const entry of manifest.files) {
    const bytes = readFixture(entry.world);
    const world = core.parseWorldText(bytes.toString("utf8"));
    assert.equal(world.seed, entry.seed);
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    assert.equal(core.toHex(digest), entry.world_sha256);
    const events = core.parseNdjson(readFixture(entry.name).toString("utf8"));
    assert.equal(core.homeTeam(events), world.home);
    assert.deepEqual(core.unknownIds(events, world), []);
    const names = core.nameIndex(world, world.home);
    assert.equal(names.club(world.home), world.clubs.find((c) => c.club_id === world.home).name);
  }
});

test("unsafe world names never become URLs", () => {
  assert.equal(core.worldUrl("data/", "1.world.json"), "data/1.world.json");
  for (const bad of ["../1.world.json", "a/1.world.json", "https://x.example/1.world.json", "1.json", "1.ndjson", "", null]) {
    assert.throws(() => core.worldUrl("data/", bad), /unsafe/);
  }
});

test("resolveManifest prefers data/ when it is valid", async () => {
  const r = await core.resolveManifest(fakeFetch({ "data/manifest.json": { status: 200, body: GOOD } }));
  assert.equal(r.source, "data");
  assert.equal(r.base, "data/");
  assert.equal(r.notice, null);
});

test("resolveManifest falls back to the fixture on 404, an HTML page or a network error", async () => {
  const fixture = { "data-fixture/manifest.json": { status: 200, body: GOOD } };
  for (const data of [undefined, { status: 200, body: "<!doctype html><title>x</title>" }, new Error("offline")]) {
    const routes = Object.assign({}, fixture);
    if (data !== undefined) {
      routes["data/manifest.json"] = data;
    }
    const r = await core.resolveManifest(fakeFetch(routes));
    assert.equal(r.source, "fixture");
    assert.equal(r.base, "data-fixture/");
    assert.equal(r.notice, null);
  }
});

test("resolveManifest says why when data/manifest.json exists but breaks the contract", async () => {
  const routes = {
    "data/manifest.json": { status: 200, body: JSON.stringify({ generator: "x", files: [] }) },
    "data-fixture/manifest.json": { status: 200, body: GOOD }
  };
  const r = await core.resolveManifest(fakeFetch(routes));
  assert.equal(r.source, "fixture");
  assert.match(r.notice, /could not be used/);
});

test("resolveManifest fails loudly when neither manifest loads", async () => {
  await assert.rejects(core.resolveManifest(fakeFetch({})), /No match data could be loaded/);
});

/* ------------------------------------------------------------------
   Runs (web/agon-runs.js): the Newsroom Wall, the stand-in card, the
   recap and the Verify bundle check. Kept in this file because the
   web-check workflow runs exactly this file.
   ------------------------------------------------------------------ */

const runsApi = require(path.join(ROOT, "web", "agon-runs.js"));
const RUNS = path.join(ROOT, "web", "runs");
const LABEL = "Design preview: sample run written by hand, not produced by the pipeline.";

function sampleRun() {
  return JSON.parse(fs.readFileSync(path.join(RUNS, "sample-s1.json"), "utf8"));
}

function applyCase(run, c) {
  if (Object.prototype.hasOwnProperty.call(c, "replace")) {
    return c.replace;
  }
  const out = JSON.parse(JSON.stringify(run));
  for (const op of c.ops) {
    let target = out;
    for (const key of op.path.slice(0, -1)) {
      target = target[key];
    }
    const last = op.path[op.path.length - 1];
    if (op.delete) {
      delete target[last];
    } else {
      target[last] = op.value;
    }
  }
  return out;
}

test("run rules agree with the Python validator on the shared cases", () => {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "run_cases.json"), "utf8"));
  assert.ok(cases.length >= 25);
  const run = sampleRun();
  for (const c of cases) {
    const problems = runsApi.validateRun(applyCase(run, c));
    assert.equal(problems.length === 0, c.valid, c.case + ": " + problems.join("; "));
    if (c.problem !== undefined) {
      assert.ok(problems.some((p) => p.indexOf(c.problem) !== -1), c.case + ": " + problems.join("; "));
    }
  }
});

test("the runs index is valid and names the sample", () => {
  const index = JSON.parse(fs.readFileSync(path.join(RUNS, "index.json"), "utf8"));
  assert.deepEqual(runsApi.validateIndex(index), []);
  assert.equal(index.runs[0].run_id, "sample-s1");
  assert.ok(runsApi.validateIndex({ format: "agon.web-runs/1", runs: [] }).length > 0);
});

test("the design preview label is pinned and the sample carries it", () => {
  assert.equal(runsApi.DESIGN_PREVIEW_LABEL, LABEL);
  assert.equal(sampleRun().label, LABEL);
  const html = fs.readFileSync(path.join(ROOT, "web", "index.html"), "utf8");
  assert.ok(html.indexOf(LABEL) !== -1);
  assert.ok(html.indexOf(runsApi.STANDIN_HEADER) !== -1);
});

test("the WebCrypto bundle check recomputes the sample's bundle hash and every edition", async () => {
  const run = sampleRun();
  const manifestBytes = fs.readFileSync(path.join(RUNS, "sample-s1", "manifest.json"));
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  const computed = { manifest: core.toHex(await globalThis.crypto.subtle.digest("SHA-256", manifestBytes)), files: {} };
  for (const name of Object.keys(manifest.artifacts)) {
    const bytes = fs.readFileSync(path.join(RUNS, "sample-s1", name));
    computed.files[name] = { sha256: core.toHex(await globalThis.crypto.subtle.digest("SHA-256", bytes)), bytes: bytes.length };
  }
  const good = runsApi.bundleCheck(run, manifest, computed);
  assert.equal(good.ok, true);
  assert.equal(good.rows.length, 5);

  /* A flipped byte in one edition: that row and the whole check fail. */
  const bytes = Buffer.from(fs.readFileSync(path.join(RUNS, "sample-s1", "edition-fr.json")));
  bytes[30] = bytes[30] === 0x5a ? 0x59 : 0x5a;
  const flipped = JSON.parse(JSON.stringify(computed));
  flipped.files["edition-fr.json"].sha256 = core.toHex(await globalThis.crypto.subtle.digest("SHA-256", bytes));
  const bad = runsApi.bundleCheck(run, manifest, flipped);
  assert.equal(bad.ok, false);
  assert.equal(bad.rows.find((r) => r.name === "edition-fr.json").status, "mismatch");

  /* A manifest that hashes differently from the card's hash fails too. */
  const other = runsApi.bundleCheck(run, manifest, Object.assign({}, computed, { manifest: "0".repeat(64) }));
  assert.equal(other.bundleMatches, false);
  assert.equal(other.ok, false);

  /* A file that could not be fetched is reported, never skipped. */
  const missing = JSON.parse(JSON.stringify(computed));
  delete missing.files["edition-el.json"];
  const gone = runsApi.bundleCheck(run, manifest, missing);
  assert.equal(gone.rows.find((r) => r.name === "edition-el.json").status, "error");
  assert.equal(gone.ok, false);
});

test("Approve names every unticked Confirm row, then explains that nothing was sent", () => {
  const run = sampleRun();
  const none = runsApi.approveAttempt(run, []);
  assert.equal(none.ok, false);
  assert.equal(none.message, "Tick the referee sentence and the injury sentence before approving.");
  const one = runsApi.approveAttempt(run, ["confirm-en-04"]);
  assert.equal(one.message, "Tick the injury sentence before approving.");
  const all = runsApi.approveAttempt(run, ["confirm-en-04", "confirm-en-05"]);
  assert.equal(all.ok, true);
  assert.match(all.message, /^Nothing was approved or published\. The decision endpoint is not deployed yet/);
  assert.ok(all.message.indexOf(run.bundle.sha256.slice(0, 12)) !== -1);
  assert.match(runsApi.notDeployedMessage("changes", run), /^Nothing was sent\. The decision endpoint is not deployed yet/);
});

test("attestation counts come from the sealed manifest and the Confirm rows", () => {
  const run = sampleRun();
  const manifest = JSON.parse(fs.readFileSync(path.join(RUNS, "sample-s1", "manifest.json"), "utf8"));
  const att = runsApi.attestation(run, manifest);
  assert.equal(att.attesting, "You are attesting: headline report and 2 flagged sentences");
  assert.equal(att.cleared, "Gate-cleared only: 4 editions");
});

test("the approved edition label appears only on an approved pipeline run", () => {
  const run = sampleRun();
  assert.ok(runsApi.editionLabel(run).indexOf(LABEL) !== -1);
  assert.equal(runsApi.editionLabel(run).indexOf(runsApi.APPROVED_LABEL_PREFIX), -1);
  const approved = JSON.parse(JSON.stringify(run));
  approved.provenance = "pipeline";
  approved.label = "Recorded run, 2026-10-23, commit 0123abc";
  approved.state = "APPROVED";
  approved.states[3].state = "APPROVED";
  approved.approval = { status: "approved", approved_at: "2026-10-23T16:05:00Z", approver_role: "media_officer", channel: "teams" };
  assert.deepEqual(runsApi.validateRun(approved), []);
  assert.equal(runsApi.editionLabel(approved),
    "AI-drafted. Numbers and names checked by code. Headline report and flagged sentences approved by the club media office at 16:05 UTC on 2026-10-23.");
  const awaiting = JSON.parse(JSON.stringify(approved));
  awaiting.state = "AWAITING_APPROVAL";
  awaiting.states[3].state = "AWAITING_APPROVAL";
  awaiting.approval = { status: "awaiting", approved_at: null, approver_role: null, channel: null };
  assert.equal(runsApi.editionLabel(awaiting).indexOf(runsApi.APPROVED_LABEL_PREFIX), -1);
});

test("the Why drawer lists event ids, claim types, gate verdicts and the author tag", () => {
  const run = sampleRun();
  const edition = JSON.parse(fs.readFileSync(path.join(RUNS, "sample-s1", "edition-en.json"), "utf8"));
  const meta = run.editions.find((e) => e.edition === "en");
  const w = runsApi.why(run, meta, edition.sentences[4], 4);
  assert.equal(w.sentenceId, "en-05");
  assert.deepEqual(w.eventIds, ["1407b818a8237c56"]);
  assert.deepEqual(w.claimTypes, ["Event", "Minute", "Name"]);
  assert.deepEqual(w.gates.map((g) => g.name + ": " + g.words), ["Fact Gate: Pass", "Rights Guard: Pass"]);
  assert.equal(w.authorTag, "hand");
  assert.match(w.author, /Written by hand/);
  assert.equal(w.type, "Injury or medical");
  assert.match(w.scope, /Confirm row/);
  const easy = JSON.parse(fs.readFileSync(path.join(RUNS, "sample-s1", "edition-easy.json"), "utf8"));
  const we = runsApi.why(run, run.editions.find((e) => e.edition === "easy"), easy.sentences[0], 0);
  assert.match(we.scope, /^Gate-cleared only/);
  assert.equal(we.eventIds.length, 0);
});

test("the wall walks DRAFT, CHECKED, SEALED, AWAITING_APPROVAL in step order", () => {
  const run = sampleRun();
  assert.equal(runsApi.stateAt(run, 0), "");
  assert.equal(runsApi.stateWords(runsApi.stateAt(run, 0)), "No bundle yet");
  const seen = [];
  for (let step = 1; step <= runsApi.lastStep(run); step += 1) {
    const s = runsApi.stateAt(run, step);
    if (s && seen[seen.length - 1] !== s) {
      seen.push(s);
    }
  }
  assert.deepEqual(seen, runsApi.WALL_STATES);
  const items = runsApi.wallItems(run);
  for (let i = 1; i < items.length; i += 1) {
    assert.ok(items[i - 1].step <= items[i].step);
  }
  assert.ok(items.some((x) => x.kind === "handoff" && x.to === "Approval card"));
  for (const item of items) {
    assert.doesNotMatch(item.text, /\bC\d+P\d+\b/, "an id leaked into the wall: " + item.text);
  }
});

test("run and bundle URLs refuse unsafe names", () => {
  const run = sampleRun();
  assert.equal(runsApi.runUrl("sample-s1"), "runs/sample-s1.json");
  assert.equal(runsApi.bundleFileUrl(run, "edition-en.json"), "runs/sample-s1/edition-en.json");
  for (const bad of ["../x", "A", "", "a/b"]) {
    assert.throws(() => runsApi.runUrl(bad), /unsafe/);
  }
  for (const bad of ["../manifest.json", "index.html", "edition-en.json/x"]) {
    assert.throws(() => runsApi.bundleFileUrl(run, bad), /unsafe/);
  }
});
