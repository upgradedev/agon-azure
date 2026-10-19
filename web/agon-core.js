/*
  Agon core: pure functions shared by the Match Centre and the Verify page.

  No DOM access here, so the same file runs in the browser (as
  window.AgonCore) and under node --test (as a CommonJS module). The tests in
  tests/js/ hold it to the Python side: the frame conversion must agree with
  agon.synth.schema.to_fixed_frame on every fixture event, and the manifest
  rules must agree with agon.webdata.validate_manifest on a shared case list.

  Data contract (web/data-fixture/README.txt):
    manifest.json = {"generator": str,
                     "files": [{"name": str, "sha256": str, "seed": int, "events": int}]}
    <seed>.ndjson = one canonical event per line.
*/
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    root.AgonCore = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var PITCH_X_DM = 1050;
  var PITCH_Y_DM = 680;
  var HALF_SECONDS = 2700;
  var DATA_BASE = "data/";
  var FIXTURE_BASE = "data-fixture/";
  var MANIFEST_NAME = "manifest.json";
  var MANIFEST_KEYS = ["files", "generator"];
  var FILE_KEYS = ["events", "name", "seed", "sha256"];
  var FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*\.ndjson$/;
  var SHA256_HEX = /^[0-9a-f]{64}$/;
  var ON_BALL = { pass: true, carry: true, shot: true };
  var PRESSURE_WINDOW_SECONDS = 300;

  /* ---------------------------------------------------------- manifest */

  function isInt(value) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value;
  }

  function sameKeys(obj, expected) {
    var keys = Object.keys(obj).sort();
    if (keys.length !== expected.length) {
      return false;
    }
    for (var i = 0; i < keys.length; i += 1) {
      if (keys[i] !== expected[i]) {
        return false;
      }
    }
    return true;
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  /* Same rules as agon.webdata.validate_manifest. Returns a list of problems. */
  function validateManifest(manifest) {
    if (!isObject(manifest)) {
      return ["manifest must be a JSON object"];
    }
    var problems = [];
    if (!sameKeys(manifest, MANIFEST_KEYS)) {
      problems.push("manifest keys must be exactly files and generator");
    }
    if (typeof manifest.generator !== "string" || !manifest.generator) {
      problems.push("generator must be a non-empty string");
    }
    var files = manifest.files;
    if (!Array.isArray(files) || files.length === 0) {
      problems.push("files must be a non-empty list");
      return problems;
    }
    var seen = {};
    for (var i = 0; i < files.length; i += 1) {
      var entry = files[i];
      var where = "files[" + i + "]";
      if (!isObject(entry)) {
        problems.push(where + " must be an object");
        continue;
      }
      if (!sameKeys(entry, FILE_KEYS)) {
        problems.push(where + " keys must be exactly events, name, seed and sha256");
        continue;
      }
      if (!isSafeFileName(entry.name)) {
        problems.push(where + ".name must be a bare <seed>.ndjson file name");
      } else if (Object.prototype.hasOwnProperty.call(seen, entry.name)) {
        problems.push(where + ".name " + entry.name + " is listed twice");
      } else {
        seen[entry.name] = true;
      }
      if (typeof entry.sha256 !== "string" || !SHA256_HEX.test(entry.sha256)) {
        problems.push(where + ".sha256 must be 64 lowercase hex characters");
      }
      if (!isInt(entry.seed) || entry.seed < 0) {
        problems.push(where + ".seed must be a non-negative integer");
      }
      if (!isInt(entry.events) || entry.events < 1) {
        problems.push(where + ".events must be a positive integer");
      }
    }
    return problems;
  }

  /* A bare file name only: the browser builds a same-origin URL from it. */
  function isSafeFileName(name) {
    return typeof name === "string" && FILE_NAME.test(name);
  }

  function fileUrl(base, name) {
    if (!isSafeFileName(name)) {
      throw new Error("refusing an unsafe file name in the manifest");
    }
    return base + name;
  }

  /* An HTML page where JSON or NDJSON was expected means the host served its
     fallback page for a missing file. Treated exactly like a 404. */
  function looksLikeHtml(text) {
    return /^\s*</.test(text);
  }

  function readText(fetchFn, url) {
    return fetchFn(url, { cache: "no-store" }).then(function (response) {
      return response.text().then(function (text) {
        return { status: response.status, ok: response.ok, text: text };
      });
    });
  }

  function parseManifestText(text) {
    var manifest = JSON.parse(text);
    var problems = validateManifest(manifest);
    if (problems.length) {
      throw new Error(problems.join("; "));
    }
    return manifest;
  }

  /* Resolve where the match files come from: data/ first, then the committed
     fixture. Resolves to {source, base, manifest, manifestUrl, notice}. The
     notice is set when data/manifest.json exists but could not be used, so
     the page can say why it fell back. */
  function resolveManifest(fetchFn) {
    var dataUrl = DATA_BASE + MANIFEST_NAME;
    var fixtureUrl = FIXTURE_BASE + MANIFEST_NAME;

    function fromFixture(notice) {
      return readText(fetchFn, fixtureUrl).then(function (res) {
        if (!res.ok || looksLikeHtml(res.text)) {
          throw new Error("No match data could be loaded: " + fixtureUrl + " answered " + res.status + ".");
        }
        var manifest;
        try {
          manifest = parseManifestText(res.text);
        } catch (error) {
          throw new Error("The fixture manifest is not valid: " + error.message);
        }
        return { source: "fixture", base: FIXTURE_BASE, manifest: manifest, manifestUrl: fixtureUrl, notice: notice };
      });
    }

    return readText(fetchFn, dataUrl).then(
      function (res) {
        if (!res.ok || looksLikeHtml(res.text)) {
          return fromFixture(null);
        }
        var manifest;
        try {
          manifest = parseManifestText(res.text);
        } catch (error) {
          return fromFixture(dataUrl + " was found but could not be used (" + error.message + "), so the committed fixture is shown instead.");
        }
        return { source: "data", base: DATA_BASE, manifest: manifest, manifestUrl: dataUrl, notice: null };
      },
      function () {
        return fromFixture(null);
      }
    );
  }

  /* ---------------------------------------------------------- events */

  function parseNdjson(text) {
    var lines = text.split("\n");
    var events = [];
    for (var i = 0; i < lines.length; i += 1) {
      var line = lines[i].replace(/\r$/, "");
      if (!line) {
        continue;
      }
      var event = JSON.parse(line);
      if (!isObject(event) || !isInt(event.seq) || !isInt(event.period) || !isInt(event.minute) ||
          !isInt(event.second) || typeof event.type !== "string" || typeof event.team !== "string" ||
          !isInt(event.x_dm) || !isInt(event.y_dm)) {
        throw new Error("line " + (i + 1) + " is not an event of the published schema");
      }
      events.push(event);
    }
    if (!events.length) {
      throw new Error("the file holds no events");
    }
    events.sort(function (a, b) {
      return a.seq - b.seq;
    });
    return events;
  }

  function countLines(text) {
    var count = 0;
    var lines = text.split("\n");
    for (var i = 0; i < lines.length; i += 1) {
      if (lines[i].replace(/\r$/, "")) {
        count += 1;
      }
    }
    return count;
  }

  /* The generator gives the first-half kick-off to the home club, so the team
     of the first event is the home club (README.txt, rule 2). */
  function homeTeam(events) {
    return events[0].team;
  }

  function awayTeam(events, home) {
    for (var i = 0; i < events.length; i += 1) {
      if (events[i].team !== home) {
        return events[i].team;
      }
    }
    return "";
  }

  /* Port of agon.synth.schema.to_fixed_frame: the home club attacks towards
     x = 1050 in both halves; every other team's events are rotated 180
     degrees on BOTH axes, end point included. Never flipped by period. */
  function toFixedFrame(event, homeId) {
    var x = event.x_dm;
    var y = event.y_dm;
    var ex = event.end_x_dm === undefined ? null : event.end_x_dm;
    var ey = event.end_y_dm === undefined ? null : event.end_y_dm;
    if (event.team === homeId) {
      return [x, y, ex, ey];
    }
    if (ex === null || ey === null) {
      return [PITCH_X_DM - x, PITCH_Y_DM - y, null, null];
    }
    return [PITCH_X_DM - x, PITCH_Y_DM - y, PITCH_X_DM - ex, PITCH_Y_DM - ey];
  }

  /* ---------------------------------------------------------- clock */

  function clockOf(event) {
    return event.minute * 60 + event.second;
  }

  /* A replay axis in seconds that never runs backwards. The first half runs
     from 0 to its last event; the second half follows straight after, since
     its clock restarts at 45:00 while first-half stoppage also ran past 45:00. */
  function buildTimeline(events) {
    var p1End = 0;
    var hasP2 = false;
    var i;
    for (i = 0; i < events.length; i += 1) {
      if (events[i].period === 1) {
        p1End = Math.max(p1End, clockOf(events[i]) + 1);
      } else {
        hasP2 = true;
      }
    }
    var times = [];
    for (i = 0; i < events.length; i += 1) {
      var e = events[i];
      times.push(e.period === 1 ? clockOf(e) : p1End + Math.max(0, clockOf(e) - HALF_SECONDS));
    }
    var last = times.length ? times[times.length - 1] : 0;
    return { times: times, p1End: p1End, hasSecondHalf: hasP2, total: last + 1 };
  }

  /* Map a replay time back to the match clock: {period, clock}. */
  function matchClockAt(t, timeline) {
    if (t < timeline.p1End || !timeline.hasSecondHalf) {
      return { period: 1, clock: Math.max(0, Math.floor(t)) };
    }
    return { period: 2, clock: HALF_SECONDS + Math.floor(t - timeline.p1End) };
  }

  /* Football minutes: 0:00 to 0:59 is 1'. Stoppage shows as 45+n or 90+n. */
  function formatMinute(period, clock) {
    var minute = Math.floor(clock / 60) + 1;
    if (period === 1 && minute > 45) {
      return "45+" + (minute - 45) + "'";
    }
    if (period === 2 && minute > 90) {
      return "90+" + (minute - 90) + "'";
    }
    return minute + "'";
  }

  function eventMinute(event) {
    return formatMinute(event.period, clockOf(event));
  }

  /* Index of the last event at or before replay time t, or -1. */
  function indexAt(times, t) {
    var lo = 0;
    var hi = times.length - 1;
    var found = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (times[mid] <= t) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  /* ---------------------------------------------------------- derived */

  function isGoal(event) {
    return event.type === "shot" && event.outcome === "goal";
  }

  /* Score after each event, as two arrays of running totals. */
  function runningScore(events, home) {
    var homeGoals = [];
    var awayGoals = [];
    var h = 0;
    var a = 0;
    for (var i = 0; i < events.length; i += 1) {
      if (isGoal(events[i])) {
        if (events[i].team === home) {
          h += 1;
        } else {
          a += 1;
        }
      }
      homeGoals.push(h);
      awayGoals.push(a);
    }
    return { home: homeGoals, away: awayGoals };
  }

  function scoreAt(score, index) {
    if (index < 0) {
      return { home: 0, away: 0 };
    }
    return { home: score.home[index], away: score.away[index] };
  }

  var SHOT_WORDS = { goal: "Goal", saved: "Shot saved", off_target: "Shot off target", blocked: "Shot blocked" };
  var CARD_WORDS = { yellow: "Yellow card", second_yellow: "Second yellow card", red: "Red card" };

  /* Goals, shots, cards and substitutions, in match order. */
  function keyMoments(events, timeline) {
    var out = [];
    for (var i = 0; i < events.length; i += 1) {
      var e = events[i];
      var kind = null;
      var text = null;
      if (e.type === "shot") {
        kind = isGoal(e) ? "goal" : "shot";
        text = (SHOT_WORDS[e.outcome] || "Shot") + ", " + e.team + " " + (e.player || "");
      } else if (e.type === "card") {
        kind = "card";
        text = (CARD_WORDS[e.outcome] || "Card") + ", " + e.team + " " + (e.player || "");
      } else if (e.type === "sub") {
        kind = "sub";
        text = "Substitution, " + e.team + ": " + (e.related_player || "") + " on for " + (e.player || "");
      }
      if (kind) {
        out.push({ kind: kind, index: i, time: timeline.times[i], minute: eventMinute(e), text: text.replace(/\s+$/, "") });
      }
    }
    return out;
  }

  /* Share of on-ball actions (pass, carry, shot) made under pressure, per
     team, over the last five minutes of replay time up to index. Returns
     {home: {under, total}, away: {under, total}}. */
  function pressureWindow(events, timeline, index, home) {
    var result = { home: { under: 0, total: 0 }, away: { under: 0, total: 0 } };
    if (index < 0) {
      return result;
    }
    var from = timeline.times[index] - PRESSURE_WINDOW_SECONDS;
    for (var i = index; i >= 0 && timeline.times[i] >= from; i -= 1) {
      var e = events[i];
      if (!ON_BALL[e.type]) {
        continue;
      }
      var side = e.team === home ? result.home : result.away;
      side.total += 1;
      if (e.under_pressure === true) {
        side.under += 1;
      }
    }
    return result;
  }

  function percent(part) {
    return part.total ? Math.round((100 * part.under) / part.total) + "%" : "no actions";
  }

  /* Ball position in fixed-frame decimetres at replay time t. With tween on,
     a pass, carry or shot travels from its start to its end until the next
     event; with tween off (reduced motion) the ball jumps event by event. */
  function ballAt(events, timeline, t, home, tween) {
    var i = indexAt(timeline.times, t);
    if (i < 0) {
      return { x: PITCH_X_DM / 2, y: PITCH_Y_DM / 2, index: -1 };
    }
    var p = toFixedFrame(events[i], home);
    if (!tween || p[2] === null) {
      return { x: p[0], y: p[1], index: i };
    }
    var start = timeline.times[i];
    var next = i + 1 < timeline.times.length ? timeline.times[i + 1] : start + 1;
    var span = Math.max(1, next - start);
    var f = Math.min(1, Math.max(0, (t - start) / span));
    return { x: p[0] + (p[2] - p[0]) * f, y: p[1] + (p[3] - p[1]) * f, index: i };
  }

  /* ---------------------------------------------------------- hashing */

  function toHex(buffer) {
    var bytes = new Uint8Array(buffer);
    var out = "";
    for (var i = 0; i < bytes.length; i += 1) {
      out += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    }
    return out;
  }

  return {
    PITCH_X_DM: PITCH_X_DM,
    PITCH_Y_DM: PITCH_Y_DM,
    DATA_BASE: DATA_BASE,
    FIXTURE_BASE: FIXTURE_BASE,
    MANIFEST_NAME: MANIFEST_NAME,
    validateManifest: validateManifest,
    isSafeFileName: isSafeFileName,
    fileUrl: fileUrl,
    looksLikeHtml: looksLikeHtml,
    resolveManifest: resolveManifest,
    parseNdjson: parseNdjson,
    countLines: countLines,
    homeTeam: homeTeam,
    awayTeam: awayTeam,
    toFixedFrame: toFixedFrame,
    buildTimeline: buildTimeline,
    matchClockAt: matchClockAt,
    formatMinute: formatMinute,
    eventMinute: eventMinute,
    indexAt: indexAt,
    runningScore: runningScore,
    scoreAt: scoreAt,
    keyMoments: keyMoments,
    pressureWindow: pressureWindow,
    percent: percent,
    ballAt: ballAt,
    toHex: toHex
  };
});
