/*
  Agon runs: pure functions for the Newsroom Wall (S2), the stand-in
  approval card (S3), the recap with its Why drawer (S4) and the run bundle
  check on Verify (S5).

  No DOM access, so the same file runs in the browser (as window.AgonRuns)
  and under node --test (tests/js/core.test.js). The run format is defined
  in src/agon/webrun.py and documented in web/runs/README.txt; validateRun
  applies the same rules as agon.webrun.validate_run, held to one case list
  (tests/js/run_cases.json).
*/
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    root.AgonRuns = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var RUNS_BASE = "runs/";
  var INDEX_NAME = "index.json";
  var RUN_FORMAT = "agon.web-run/1";
  var INDEX_FORMAT = "agon.web-runs/1";
  var BUNDLE_MANIFEST = "manifest.json";
  /* Pinned with agon.webrun.DESIGN_PREVIEW_LABEL, ci/web_check.py,
     ci/readiness.json and web/index.html; tests fail if they drift. */
  var DESIGN_PREVIEW_LABEL = "Design preview: sample run written by hand, not produced by the pipeline.";
  var STANDIN_HEADER = "Stand-in for the Microsoft Teams card. It calls the same decision endpoint. The real Teams card is in the video.";
  var APPROVED_LABEL_PREFIX = "AI-drafted. Numbers and names checked by code. Headline report and flagged sentences approved by the club media office at ";
  var STATES = ["DRAFT", "CHECKED", "SEALED", "AWAITING_APPROVAL", "APPROVED", "PUBLISHED", "CHANGES_REQUESTED"];
  /* The four states the wall walks through before a person decides. */
  var WALL_STATES = ["DRAFT", "CHECKED", "SEALED", "AWAITING_APPROVAL"];
  var STATE_WORDS = {
    DRAFT: "Draft",
    CHECKED: "Checked",
    SEALED: "Sealed",
    AWAITING_APPROVAL: "Awaiting approval",
    APPROVED: "Approved",
    PUBLISHED: "Published",
    CHANGES_REQUESTED: "Changes requested"
  };
  var APPROVAL_STATUSES = ["awaiting", "approved", "changes_requested"];
  var SENTENCE_TYPES = ["match_stat", "injury_medical", "disciplinary_referee", "quote_attribution", "transfer_contract", "neutral_colour"];
  var CONFIRM_TYPES = {
    injury_medical: "the injury sentence",
    disciplinary_referee: "the referee sentence",
    quote_attribution: "the quote sentence",
    transfer_contract: "the transfer sentence"
  };
  var TYPE_WORDS = {
    match_stat: "Match statistic",
    injury_medical: "Injury or medical",
    disciplinary_referee: "Discipline or referee",
    quote_attribution: "Quote",
    transfer_contract: "Transfer or contract",
    neutral_colour: "Colour"
  };
  var CLAIM_WORDS = { score: "Score", stat: "Statistic", minute: "Minute", name: "Name", event_ref: "Event" };
  var AUTHOR_WORDS = {
    hand: "Written by hand for this design preview. No model wrote it.",
    model: "Written by a Microsoft Foundry agent.",
    template: "A template line written by code.",
    officer: "Edited by the club media officer."
  };
  var GATE_VERDICTS = ["pass", "mismatch", "refused", "skipped"];
  var VERDICT_WORDS = { pass: "Pass", mismatch: "Mismatch", refused: "Refused", skipped: "Skipped" };
  var GATE_WORDS = { factgate: "Fact Gate", factgate_lang: "Fact Gate, translation", rights: "Rights Guard" };

  var RUN_ID = /^[a-z0-9][a-z0-9-]{0,62}$/;
  var EDITION_FILE = /^edition-[a-z]{2,8}\.json$/;
  var SENTENCE_ID = /^[a-z]{2,8}-[0-9]{2}$/;
  var SHA256_HEX = /^[0-9a-f]{64}$/;
  var ISO_TIME = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?Z$/;
  var RUN_KEYS = ["approval", "away", "bundle", "confirm_rows", "desk_editor_flags", "desks", "editions", "format",
    "gates", "generator", "handoffs", "home", "label", "match_id", "provenance", "run_id", "score", "seed",
    "sentence_gates", "sentence_types", "state", "states"];

  function has(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isInt(value) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value;
  }

  function nonEmpty(value) {
    return typeof value === "string" && value.replace(/\s+/g, "") !== "";
  }

  function keysAre(obj, keys) {
    if (!isObject(obj)) {
      return false;
    }
    var actual = Object.keys(obj).sort();
    var expected = keys.slice().sort();
    if (actual.length !== expected.length) {
      return false;
    }
    for (var i = 0; i < actual.length; i += 1) {
      if (actual[i] !== expected[i]) {
        return false;
      }
    }
    return true;
  }

  /* ---------------------------------------------------------- validation */

  function validateIndex(index) {
    if (!keysAre(index, ["format", "runs"])) {
      return ["runs index keys must be exactly format and runs"];
    }
    var problems = [];
    if (index.format !== INDEX_FORMAT) {
      problems.push("runs index format must be " + INDEX_FORMAT);
    }
    if (!Array.isArray(index.runs) || index.runs.length === 0) {
      problems.push("runs index must list at least one run");
      return problems;
    }
    var seen = {};
    index.runs.forEach(function (row, i) {
      var where = "runs[" + i + "]";
      if (!keysAre(row, ["file", "provenance", "run_id"])) {
        problems.push(where + " keys must be exactly file, provenance and run_id");
        return;
      }
      if (typeof row.run_id !== "string" || !RUN_ID.test(row.run_id)) {
        problems.push(where + ".run_id must be lowercase letters, digits and hyphens");
      } else if (has(seen, row.run_id)) {
        problems.push(where + ".run_id " + row.run_id + " is listed twice");
      } else {
        seen[row.run_id] = true;
        if (row.file !== row.run_id + ".json") {
          problems.push(where + ".file must be " + row.run_id + ".json");
        }
      }
      if (row.provenance !== "design_preview" && row.provenance !== "pipeline") {
        problems.push(where + ".provenance must be design_preview or pipeline");
      }
    });
    return problems;
  }

  /* Same rules and the same problem wording as agon.webrun.validate_run. */
  function validateRun(run) {
    if (!isObject(run)) {
      return ["run must be a JSON object"];
    }
    if (!keysAre(run, RUN_KEYS)) {
      var missing = RUN_KEYS.filter(function (k) { return !has(run, k); });
      var extra = Object.keys(run).filter(function (k) { return RUN_KEYS.indexOf(k) === -1; }).sort();
      return ["run keys differ: missing " + (missing.join(", ") || "none") + "; unexpected " + (extra.join(", ") || "none")];
    }
    var problems = [];
    if (run.format !== RUN_FORMAT) {
      problems.push("run format must be " + RUN_FORMAT);
    }
    if (typeof run.run_id !== "string" || !RUN_ID.test(run.run_id)) {
      problems.push("run_id must be lowercase letters, digits and hyphens");
    }
    var provenance = run.provenance;
    if (provenance !== "design_preview" && provenance !== "pipeline") {
      problems.push("provenance must be design_preview or pipeline");
    }
    if (provenance === "design_preview" && run.label !== DESIGN_PREVIEW_LABEL) {
      problems.push("a design preview run must carry the exact design preview label");
    }
    if (provenance === "pipeline" && (!nonEmpty(run.label) || run.label === DESIGN_PREVIEW_LABEL)) {
      problems.push("a pipeline run must carry its own run label");
    }
    if (!isInt(run.seed) || run.seed < 0) {
      problems.push("seed must be a non-negative integer");
    }
    ["match_id", "generator"].forEach(function (key) {
      if (!nonEmpty(run[key])) {
        problems.push(key + " must be a non-empty string");
      }
    });
    ["home", "away"].forEach(function (side) {
      if (!keysAre(run[side], ["club_id", "name"]) || !nonEmpty(run[side].name)) {
        problems.push(side + " must be {club_id, name} with a name");
      }
    });
    var score = run.score;
    if (!keysAre(score, ["away", "home"]) || !isInt(score.home) || !isInt(score.away) || score.home < 0 || score.away < 0) {
      problems.push("score must be {home, away} with non-negative integers");
    }
    var bundle = run.bundle;
    if (!keysAre(bundle, ["dir", "manifest", "sha256"])) {
      problems.push("bundle keys must be exactly dir, manifest and sha256");
    } else {
      if (typeof bundle.sha256 !== "string" || !SHA256_HEX.test(bundle.sha256)) {
        problems.push("bundle.sha256 must be 64 lowercase hex characters");
      }
      if (bundle.dir !== run.run_id) {
        problems.push("bundle.dir must equal run_id");
      }
      if (bundle.manifest !== BUNDLE_MANIFEST) {
        problems.push("bundle.manifest must be " + BUNDLE_MANIFEST);
      }
    }
    if (STATES.indexOf(run.state) === -1) {
      problems.push("state must be one of " + STATES.join(", "));
    }
    var states = run.states;
    if (!Array.isArray(states) || states.length === 0) {
      problems.push("states must be a non-empty list");
    } else {
      var steps = [];
      states.forEach(function (row, i) {
        if (!keysAre(row, ["state", "step"]) || STATES.indexOf(row.state) === -1 || !isInt(row.step)) {
          problems.push("states[" + i + "] must be {state, step} with a known state");
        } else {
          steps.push(row.step);
        }
      });
      for (var s = 1; s < steps.length; s += 1) {
        if (steps[s] < steps[s - 1]) {
          problems.push("states must be in step order");
          break;
        }
      }
      var last = states[states.length - 1];
      if (isObject(last) && last.state !== run.state) {
        problems.push("the last entry in states must be the current state");
      }
    }
    var deskIds = {};
    if (!Array.isArray(run.desks) || run.desks.length === 0) {
      problems.push("desks must be a non-empty list");
    } else {
      run.desks.forEach(function (desk, i) {
        if (!keysAre(desk, ["desk", "lines", "name"]) || !nonEmpty(desk.name)) {
          problems.push("desks[" + i + "] must be {desk, name, lines}");
          return;
        }
        deskIds[desk.desk] = true;
        var ok = Array.isArray(desk.lines) && desk.lines.length > 0 && desk.lines.every(function (line) {
          return keysAre(line, ["step", "text"]) && isInt(line.step) && nonEmpty(line.text);
        });
        if (!ok) {
          problems.push("desks[" + i + "].lines must be a non-empty list of {step, text}");
        }
      });
    }
    if (!Array.isArray(run.handoffs)) {
      problems.push("handoffs must be a list");
    } else {
      run.handoffs.forEach(function (h, i) {
        if (!keysAre(h, ["from", "step", "text", "to"]) || !isInt(h.step) || !nonEmpty(h.text)) {
          problems.push("handoffs[" + i + "] must be {step, from, to, text}");
        } else if (!has(deskIds, h.from) || (!has(deskIds, h.to) && h.to !== "approval")) {
          problems.push("handoffs[" + i + "] names a desk that is not on the wall");
        }
      });
    }
    if (!Array.isArray(run.gates) || run.gates.length === 0) {
      problems.push("gates must be a non-empty list");
    } else {
      run.gates.forEach(function (g, i) {
        if (!keysAre(g, ["detail", "evidence", "gate", "name", "verdict"]) || GATE_VERDICTS.indexOf(g.verdict) === -1) {
          problems.push("gates[" + i + "] must be {gate, name, verdict, detail, evidence} with a known verdict");
        }
      });
    }
    var editionIds = {};
    if (!Array.isArray(run.editions) || run.editions.length === 0) {
      problems.push("editions must be a non-empty list");
    } else {
      run.editions.forEach(function (e, i) {
        if (!keysAre(e, ["edition", "file", "language", "name"])) {
          problems.push("editions[" + i + "] keys must be exactly edition, file, language and name");
          return;
        }
        if (typeof e.file !== "string" || !EDITION_FILE.test(e.file) || e.file !== "edition-" + e.edition + ".json") {
          problems.push("editions[" + i + "].file must be edition-<edition>.json");
        }
        if (has(editionIds, e.edition)) {
          problems.push("editions[" + i + "].edition " + e.edition + " is listed twice");
        }
        editionIds[e.edition] = true;
      });
    }
    var types = run.sentence_types;
    if (!isObject(types) || Object.keys(types).length === 0) {
      problems.push("sentence_types must be a non-empty map");
      types = {};
    } else {
      Object.keys(types).forEach(function (sid) {
        if (!SENTENCE_ID.test(sid) || SENTENCE_TYPES.indexOf(types[sid]) === -1) {
          problems.push("sentence_types[" + sid + "] must map a sentence id to a known type");
        }
      });
    }
    var sentenceGates = run.sentence_gates;
    if (!isObject(sentenceGates)) {
      problems.push("sentence_gates must be a map");
    } else {
      Object.keys(sentenceGates).forEach(function (sid) {
        var v = sentenceGates[sid];
        var ok = SENTENCE_ID.test(sid) && isObject(v) && Object.keys(v).length > 0 && Object.keys(v).every(function (g) {
          return GATE_VERDICTS.indexOf(v[g]) !== -1;
        });
        if (!ok) {
          problems.push("sentence_gates[" + sid + "] must map gate names to known verdicts");
        }
      });
    }
    if (!Array.isArray(run.confirm_rows)) {
      problems.push("confirm_rows must be a list");
    } else {
      var listed = {};
      run.confirm_rows.forEach(function (row, i) {
        if (!keysAre(row, ["label", "row_id", "sentence_id", "type"])) {
          problems.push("confirm_rows[" + i + "] keys must be exactly label, row_id, sentence_id and type");
          return;
        }
        if (!has(CONFIRM_TYPES, row.type) || row.label !== CONFIRM_TYPES[row.type]) {
          problems.push("confirm_rows[" + i + "] must use the routing table's type and label");
        }
        if (types[row.sentence_id] !== row.type) {
          problems.push("confirm_rows[" + i + "] does not match the type of " + row.sentence_id);
        }
        listed[row.sentence_id] = true;
      });
      var needed = Object.keys(types).filter(function (sid) {
        return has(CONFIRM_TYPES, types[sid]) && !has(listed, sid);
      }).sort();
      if (needed.length) {
        problems.push("every injury, referee, quote and transfer sentence needs a Confirm row: missing " + needed.join(", "));
      }
    }
    var flagsOk = Array.isArray(run.desk_editor_flags) && run.desk_editor_flags.every(function (f) {
      return keysAre(f, ["sentence_id", "text"]) && nonEmpty(f.text);
    });
    if (!flagsOk) {
      problems.push("desk_editor_flags must be a list of {sentence_id, text}");
    }
    var approval = run.approval;
    if (!keysAre(approval, ["approved_at", "approver_role", "channel", "status"]) || APPROVAL_STATUSES.indexOf(approval.status) === -1) {
      problems.push("approval must be {status, approved_at, approver_role, channel} with a known status");
    } else {
      var approved = approval.status === "approved";
      if (approved !== (run.state === "APPROVED" || run.state === "PUBLISHED")) {
        problems.push("approval.status and state disagree");
      }
      if (approved && (typeof approval.approved_at !== "string" || !ISO_TIME.test(approval.approved_at) ||
          !nonEmpty(approval.approver_role) || !nonEmpty(approval.channel))) {
        problems.push("an approved run must name approved_at (UTC, ending in Z), approver_role and channel");
      }
      if (!approved && approval.approved_at !== null) {
        problems.push("a run that is not approved has no approved_at");
      }
      if (approved && provenance === "design_preview") {
        problems.push("a design preview run can never be approved");
      }
    }
    return problems;
  }

  /* ---------------------------------------------------------- urls */

  function runUrl(runId) {
    if (typeof runId !== "string" || !RUN_ID.test(runId)) {
      throw new Error("refusing an unsafe run id");
    }
    return RUNS_BASE + runId + ".json";
  }

  function bundleFileUrl(run, name) {
    if (name !== BUNDLE_MANIFEST && !EDITION_FILE.test(String(name))) {
      throw new Error("refusing an unsafe bundle file name");
    }
    return RUNS_BASE + run.bundle.dir + "/" + name;
  }

  /* ---------------------------------------------------------- wall */

  function deskNames(run) {
    var names = { approval: "Approval card" };
    run.desks.forEach(function (d) {
      names[d.desk] = d.name;
    });
    return names;
  }

  /* One ordered list for the phone timeline: desk lines, handoffs and state
     changes, by step; at one step a line comes before its handoff, and the
     handoff before the state it causes. */
  function wallItems(run) {
    var names = deskNames(run);
    var items = [];
    var order = { line: 0, handoff: 1, state: 2 };
    run.desks.forEach(function (d) {
      d.lines.forEach(function (line) {
        items.push({ kind: "line", step: line.step, desk: d.desk, deskName: d.name, text: line.text });
      });
    });
    run.handoffs.forEach(function (h) {
      items.push({ kind: "handoff", step: h.step, from: names[h.from], to: names[h.to], text: h.text });
    });
    run.states.forEach(function (s) {
      items.push({ kind: "state", step: s.step, state: s.state, text: "Bundle state: " + STATE_WORDS[s.state] });
    });
    items.sort(function (a, b) {
      return a.step - b.step || order[a.kind] - order[b.kind];
    });
    return items;
  }

  function lastStep(run) {
    var max = 0;
    wallItems(run).forEach(function (item) {
      max = Math.max(max, item.step);
    });
    return max;
  }

  /* The bundle state at a step of the replay, or "" before the first draft. */
  function stateAt(run, step) {
    var current = "";
    run.states.forEach(function (s) {
      if (s.step <= step) {
        current = s.state;
      }
    });
    return current;
  }

  function stateWords(state) {
    return state ? STATE_WORDS[state] || state : "No bundle yet";
  }

  /* ---------------------------------------------------------- approval */

  function joinLabels(labels) {
    if (labels.length <= 1) {
      return labels.join("");
    }
    return labels.slice(0, -1).join(", ") + " and " + labels[labels.length - 1];
  }

  function shortHash(hash) {
    return String(hash).slice(0, 12);
  }

  /* Approve is never disabled. Clicking it either names every unticked
     Confirm row or, with all ticked, says in full why nothing was sent. */
  function approveAttempt(run, tickedRowIds) {
    var ticked = {};
    (tickedRowIds || []).forEach(function (id) {
      ticked[id] = true;
    });
    var missing = run.confirm_rows.filter(function (row) {
      return !has(ticked, row.row_id);
    });
    if (missing.length) {
      return {
        ok: false,
        missing: missing.map(function (row) { return row.row_id; }),
        message: "Tick " + joinLabels(missing.map(function (row) { return row.label; })) + " before approving."
      };
    }
    return { ok: true, missing: [], message: notDeployedMessage("approve", run) };
  }

  function notDeployedMessage(action, run) {
    var hash = shortHash(run.bundle.sha256);
    if (action === "approve") {
      return "Nothing was approved or published. The decision endpoint is not deployed yet, so this stand-in card " +
        "could not send your approval of bundle " + hash + ". Once the decision endpoint is deployed, Approve sends " +
        "that exact hash to the same endpoint the Teams card calls.";
    }
    return "Nothing was sent. The decision endpoint is not deployed yet, so this stand-in card could not send a " +
      "change request for bundle " + hash + ". Once the decision endpoint is deployed, Request changes goes to " +
      "the same endpoint the Teams card calls.";
  }

  /* "You are attesting" versus "Gate-cleared only", from the sealed manifest's
     attested scope and the run's Confirm rows. */
  function attestation(run, manifest) {
    var scope = manifest.attested_scope;
    var headline = scope.attested.indexOf("edition-en.json") !== -1;
    var flagged = run.confirm_rows.length;
    var parts = [];
    if (headline) {
      parts.push("headline report");
    }
    parts.push(flagged + " flagged sentence" + (flagged === 1 ? "" : "s"));
    var cleared = scope.gate_cleared.length;
    return {
      attesting: "You are attesting: " + parts.join(" and "),
      cleared: "Gate-cleared only: " + cleared + " edition" + (cleared === 1 ? "" : "s"),
      flagged: flagged,
      clearedCount: cleared
    };
  }

  /* ---------------------------------------------------------- recap */

  function formatUtc(iso) {
    var m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})/.exec(iso);
    if (!m) {
      return iso;
    }
    return m[4] + ":" + m[5] + " UTC on " + m[1] + "-" + m[2] + "-" + m[3];
  }

  /* The edition label. The approved wording appears only on an approved
     pipeline run; everything else says what it is. */
  function editionLabel(run) {
    if (run.provenance === "pipeline" && run.approval.status === "approved") {
      return APPROVED_LABEL_PREFIX + formatUtc(run.approval.approved_at) + ".";
    }
    if (run.provenance === "design_preview") {
      return "Not approved. " + DESIGN_PREVIEW_LABEL + " No AI wrote these editions and no media officer has approved them.";
    }
    if (run.approval.status === "changes_requested") {
      return "AI-drafted. Numbers and names checked by code. The club media office asked for changes, so this edition is not approved.";
    }
    return "AI-drafted. Numbers and names checked by code. Not yet approved by the club media office.";
  }

  function unique(list) {
    var seen = {};
    var out = [];
    list.forEach(function (x) {
      if (!has(seen, x)) {
        seen[x] = true;
        out.push(x);
      }
    });
    return out;
  }

  /* Everything the Why drawer lists for one sentence of one edition. */
  function why(run, edition, sentence, index) {
    var enIds = Object.keys(run.sentence_types).sort();
    var type = run.sentence_types[enIds[index]] || "";
    var eventIds = [];
    var claimTypes = [];
    (sentence.claims || []).forEach(function (claim) {
      claimTypes.push(CLAIM_WORDS[claim.type] || claim.type);
      (claim.event_ids || []).forEach(function (id) {
        eventIds.push(id);
      });
    });
    var verdicts = run.sentence_gates[sentence.sentence_id] || {};
    var gates = Object.keys(verdicts).sort().map(function (g) {
      return { gate: g, name: GATE_WORDS[g] || g, verdict: verdicts[g], words: VERDICT_WORDS[verdicts[g]] || verdicts[g] };
    });
    var headline = edition.edition === "en";
    var confirm = has(CONFIRM_TYPES, type);
    var scope;
    if (headline && confirm) {
      scope = "Headline report and a Confirm row: the media officer must tick it before approving.";
    } else if (headline) {
      scope = "Headline report: the media officer attests it on approval.";
    } else {
      scope = "Gate-cleared only: checked by the Fact Gate and the Rights Guard, not read by the media officer.";
    }
    return {
      sentenceId: sentence.sentence_id,
      text: sentence.text,
      eventIds: unique(eventIds),
      claimTypes: unique(claimTypes),
      gates: gates,
      author: AUTHOR_WORDS[sentence.author] || "Author tag " + sentence.author + ".",
      authorTag: sentence.author,
      type: TYPE_WORDS[type] || "Not typed",
      scope: scope
    };
  }

  /* ---------------------------------------------------------- bundle check */

  /* Compare what the browser computed with what the run and manifest state.
     computed = {manifest: hex, files: {name: {sha256, bytes}}}. */
  function bundleCheck(run, manifest, computed) {
    var rows = [];
    var listed = manifest && isObject(manifest.artifacts) ? manifest.artifacts : {};
    Object.keys(listed).sort().forEach(function (name) {
      var entry = listed[name];
      var got = computed.files[name];
      var status = "error";
      var reason = "";
      if (!got) {
        reason = "the file could not be fetched";
      } else if (got.sha256 !== entry.sha256) {
        status = "mismatch";
        reason = "the bytes received hash to a different value";
      } else if (got.bytes !== entry.bytes) {
        status = "mismatch";
        reason = "the file has " + got.bytes + " bytes, the manifest lists " + entry.bytes;
      } else {
        status = "match";
      }
      rows.push({ name: name, expected: entry.sha256, computed: got ? got.sha256 : "", status: status, reason: reason });
    });
    var bundleMatches = computed.manifest === run.bundle.sha256;
    var filesMatch = rows.length > 0 && rows.every(function (r) { return r.status === "match"; });
    return { bundleMatches: bundleMatches, filesMatch: filesMatch, ok: bundleMatches && filesMatch, rows: rows };
  }

  return {
    RUNS_BASE: RUNS_BASE,
    INDEX_NAME: INDEX_NAME,
    BUNDLE_MANIFEST: BUNDLE_MANIFEST,
    DESIGN_PREVIEW_LABEL: DESIGN_PREVIEW_LABEL,
    STANDIN_HEADER: STANDIN_HEADER,
    APPROVED_LABEL_PREFIX: APPROVED_LABEL_PREFIX,
    WALL_STATES: WALL_STATES,
    CONFIRM_TYPES: CONFIRM_TYPES,
    validateIndex: validateIndex,
    validateRun: validateRun,
    runUrl: runUrl,
    bundleFileUrl: bundleFileUrl,
    deskNames: deskNames,
    wallItems: wallItems,
    lastStep: lastStep,
    stateAt: stateAt,
    stateWords: stateWords,
    shortHash: shortHash,
    approveAttempt: approveAttempt,
    notDeployedMessage: notDeployedMessage,
    attestation: attestation,
    formatUtc: formatUtc,
    editionLabel: editionLabel,
    why: why,
    bundleCheck: bundleCheck
  };
});
