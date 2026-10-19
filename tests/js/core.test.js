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

  const moments = core.keyMoments(events, tl);
  assert.deepEqual(moments.map((m) => m.kind), ["shot", "goal", "card", "sub", "goal"]);
  assert.equal(moments[1].minute, "5'");
  assert.equal(moments[2].text, "Yellow card, A AP04");
  assert.equal(moments[3].text, "Substitution, H: HP15 on for HP07");

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
  files: [{ name: "1.ndjson", sha256: "a".repeat(64), seed: 1, events: 3 }]
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
