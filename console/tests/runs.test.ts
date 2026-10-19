/*
  The run rules, held to the shared case list tests/js/run_cases.json that
  agon.webrun.validate_run and web/agon-runs.js are held to, plus the honesty
  rules the screens depend on: the approved label only when the approval names
  the bundle hash, the approved-versus-published comparison computed from the
  recomputed hash, Approve naming unticked rows, and destinations.
*/
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DELIVERY,
  DESIGN_PREVIEW_LABEL,
  DESTINATION_LABELS,
  OUTBOX_LABEL,
  PREVIEW_LABEL,
  approveAttempt,
  attestation,
  bundleCheck,
  compareApproved,
  decidedMessage,
  editionLabel,
  isApproved,
  lastStep,
  stateAt,
  validateDestinations,
  validateIndex,
  validateRun,
  wallItems,
  runForBlow,
  why,
  withheldPayloads,
} from "../src/lib/runs";
import { chipsFor } from "../src/lib/destinations";
import { loadRunsData, type FetchText } from "../src/lib/loader";
import type { BundleManifest, Destination, Run } from "../src/lib/types";
import { CASES, RECORDED, WEB, afterFetch, diskFetch, diskFetchAt, readJson } from "./helpers";

type Op = { path: (string | number)[]; delete?: boolean; value?: unknown; from?: (string | number)[] };
type RunCase = { case: string; valid: boolean; problem?: string; ops?: Op[]; replace?: unknown };

function sampleRun(): Run {
  return readJson<Run>(join(WEB, "runs", "sample-s1.json"));
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/* Same case language as tests/js/core.test.js applyCase. */
function applyCase(run: Run, c: RunCase): unknown {
  if (Object.prototype.hasOwnProperty.call(c, "replace")) {
    return c.replace;
  }
  const out = clone(run) as unknown as Record<string | number, unknown>;
  for (const op of c.ops ?? []) {
    let target = out as Record<string | number, unknown>;
    for (const key of op.path.slice(0, -1)) {
      target = target[key] as Record<string | number, unknown>;
    }
    const last = op.path[op.path.length - 1];
    if (op.delete) {
      delete target[last];
    } else if (op.from) {
      let source: unknown = out;
      for (const key of op.from) {
        source = (source as Record<string | number, unknown>)[key];
      }
      target[last] = clone(source);
    } else {
      target[last] = op.value;
    }
  }
  return out;
}

describe("shared run cases", () => {
  it("agree with the Python validator", () => {
    const cases = readJson<RunCase[]>(join(CASES, "run_cases.json"));
    expect(cases.length).toBeGreaterThanOrEqual(25);
    const run = sampleRun();
    for (const c of cases) {
      const problems = validateRun(applyCase(run, c));
      expect(problems.length === 0, `${c.case}: ${problems.join("; ")}`).toBe(c.valid);
      if (c.problem !== undefined) {
        expect(problems.some((p) => p.includes(c.problem as string)), `${c.case}: ${problems.join("; ")}`).toBe(true);
      }
    }
  });

  it("the runs index is valid and names the sample, which carries the exact label", () => {
    const index = readJson(join(WEB, "runs", "index.json"));
    expect(validateIndex(index)).toEqual([]);
    const run = sampleRun();
    expect(run.label).toBe(DESIGN_PREVIEW_LABEL);
    expect(validateRun(run)).toEqual([]);
  });
});

/* Rows in the shape agon.publish.webview writes, for a run that is not approved
   (every offered destination not_selected) and for a published one. */
function offeredRows(): Destination[] {
  return DESTINATION_LABELS.map(([id, label]): Destination => ({
    id,
    label,
    status: "not_selected",
    preview: { kind: DELIVERY[id], note: null, title: label, text: "", payload_sha256: "a".repeat(64), path: null },
  }));
}

function publishedRows(): Destination[] {
  return offeredRows().map((row): Destination => {
    if (!row.preview) {
      return row;
    }
    if (row.preview.kind === "written") {
      return { ...row, status: "delivered", preview: { ...row.preview, path: `feed/${row.id}.json` } };
    }
    const note = row.preview.kind === "preview" ? PREVIEW_LABEL : OUTBOX_LABEL;
    return { ...row, status: "preview_not_posted", preview: { ...row.preview, note } };
  });
}

describe("destinations, held to agon.publish.webview", () => {
  it("accepts all six offered and not selected on a run that is not approved", () => {
    const run = sampleRun();
    expect(validateRun({ ...run, destinations: offeredRows() })).toEqual([]);
  });

  it("refuses the shapes destination_problems refuses, in its words", () => {
    const run = sampleRun();
    expect(validateDestinations(offeredRows().slice(0, 5), false, "AWAITING_APPROVAL")).toEqual(["destinations must list all 6 destinations"]);
    const swapped = offeredRows();
    [swapped[0], swapped[1]] = [swapped[1], swapped[0]];
    expect(validateDestinations(swapped, false, "AWAITING_APPROVAL")[0]).toBe("destinations[0].id must be club_site (registry order)");
    const relabelled = offeredRows().map((r): Destination => (r.id === "instagram" ? { ...r, label: "IG" } : r));
    expect(validateDestinations(relabelled, false, "AWAITING_APPROVAL")).toEqual(["destinations[2].label must be 'Instagram'"]);
    const early = publishedRows();
    expect(validateDestinations(early, false, "AWAITING_APPROVAL").join(" ")).toContain("a run that is not approved has no delivered or previewed destination");
    const nullPreview = offeredRows().map((r): Destination => (r.id === "feeds" ? { ...r, preview: null } : r));
    expect(validateDestinations(nullPreview, false, "AWAITING_APPROVAL")).toEqual([
      "destinations[1]: preview is null exactly when the status is not_offered",
    ]);
    expect(validateRun({ ...run, destinations: [{ id: "fax" }] })).toEqual(["destinations must list all 6 destinations"]);
  });

  it("holds delivered to a written path on a PUBLISHED run and every note to its exact sentence", () => {
    expect(validateDestinations(publishedRows(), true, "PUBLISHED")).toEqual([]);
    expect(validateDestinations(publishedRows(), true, "APPROVED").join(" ")).toContain("delivered means written bytes on a PUBLISHED run");
    const wrongNote = publishedRows().map((r): Destination => (r.id === "youtube" && r.preview ? { ...r, preview: { ...r.preview, note: "Posted." } } : r));
    expect(validateDestinations(wrongNote, true, "PUBLISHED")).toEqual([`destinations[3].preview.note must say '${PREVIEW_LABEL}'`]);
    const posted = publishedRows().map((r): Destination => (r.id === "instagram" && r.preview ? { ...r, status: "delivered" } : r));
    expect(validateDestinations(posted, true, "PUBLISHED").join(" ")).toContain("delivered means written bytes on a PUBLISHED run");
  });

  it("drives the card chips from the run, and falls back to the labelled preview without the key", () => {
    const run = sampleRun();
    const preview = chipsFor(run);
    expect(preview.fromRun).toBe(false);
    expect(preview.chips.map((c) => c.label)).toEqual([
      "Club website (CMS feed)",
      "RSS and JSON Feed",
      "Instagram",
      "YouTube",
      "Internal newsroom (Teams)",
      "Journalist email digest",
    ]);
    expect(preview.chips.filter((c) => c.note === PREVIEW_LABEL).map((c) => c.id)).toEqual(["instagram", "youtube"]);
    const notOffered = offeredRows().map((r): Destination => (r.id === "journalist_email" ? { ...r, status: "not_offered", preview: null } : r));
    const fromRun = chipsFor({ ...run, destinations: notOffered });
    expect(fromRun.fromRun).toBe(true);
    expect(fromRun.chips.find((c) => c.id === "journalist_email")?.offered).toBe(false);
    expect(fromRun.chips.every((c) => !c.ticked)).toBe(true);
  });
});

describe("approval and labels", () => {
  it("Approve names every unticked Confirm row, then says that nothing was sent and where it would have gone", () => {
    const run = sampleRun();
    const none = approveAttempt(run, []);
    expect(none.ok).toBe(false);
    expect(none.message).toBe("Tick the referee sentence and the injury sentence before approving.");
    const all = approveAttempt(run, run.confirm_rows.map((r) => r.row_id), ["Club website (CMS feed)", "RSS and JSON Feed"]);
    expect(all.ok).toBe(true);
    expect(all.message).toContain("Nothing was approved or published.");
    expect(all.message).toContain("The decision endpoint is not deployed yet");
    expect(all.message).toContain("2 destinations: Club website (CMS feed) and RSS and JSON Feed. None of them received anything.");
    const zero = approveAttempt(run, run.confirm_rows.map((r) => r.row_id), []);
    expect(zero.message).toContain("No destination is ticked");
  });

  it("shows the approved label only on a pipeline run whose approval names this bundle hash", () => {
    const run = sampleRun();
    expect(isApproved(run)).toBe(false);
    expect(editionLabel(run)).toBe(`Not approved. ${DESIGN_PREVIEW_LABEL} No AI wrote these editions and no media officer has approved them.`);
    const approved: Run = {
      ...clone(run),
      provenance: "pipeline",
      label: "Recorded run, 2026-10-20, commit abc1234",
      state: "APPROVED",
      approval: {
        status: "approved",
        approved_at: "2026-10-20T18:05:00Z",
        approver_role: "media_officer",
        channel: "teams",
        approved_bundle_sha256: run.bundle.sha256,
      },
    };
    expect(isApproved(approved)).toBe(true);
    expect(editionLabel(approved)).toContain("approved by the club media office at 18:05 UTC on 2026-10-20");
    const other = { ...approved, approval: { ...approved.approval, approved_bundle_sha256: "0".repeat(64) } };
    expect(isApproved(other)).toBe(false);
    expect(editionLabel(other)).toContain("does not name this bundle hash");
  });

  it("computes the approved-versus-published line from the recomputed hash", () => {
    const run = sampleRun();
    expect(compareApproved(run, run.bundle.sha256).kind).toBe("no-approval");
    const approved = { ...run, approval: { ...run.approval, approved_bundle_sha256: run.bundle.sha256 } };
    expect(compareApproved(approved, run.bundle.sha256).kind).toBe("equal");
    expect(compareApproved(approved, "f".repeat(64)).kind).toBe("differs");
  });

  it("counts attestation from the sealed manifest and the Confirm rows", () => {
    const run = sampleRun();
    const manifest = readJson<BundleManifest>(join(WEB, "runs", "sample-s1", "manifest.json"));
    const att = attestation(run, manifest);
    expect(att.attesting).toBe("You are attesting: headline report and 2 flagged sentences");
    expect(att.cleared).toBe("Gate-cleared only: 4 editions");
  });
});

describe("wall, why and the bundle check", () => {
  it("walks DRAFT, CHECKED, SEALED, AWAITING_APPROVAL in step order", () => {
    const run = sampleRun();
    const items = wallItems(run);
    for (let i = 1; i < items.length; i += 1) {
      expect(items[i].step).toBeGreaterThanOrEqual(items[i - 1].step);
    }
    expect(stateAt(run, 0)).toBe("");
    expect(stateAt(run, lastStep(run))).toBe("AWAITING_APPROVAL");
  });

  it("lists the cited events, claims, gates and author for a sentence", async () => {
    const data = await loadRunsData(diskFetch);
    const loaded = data.runs["sample-s1"];
    const en = loaded.editions.en;
    const w = why(loaded.run, en.meta, en.data.sentences[4], 4);
    expect(w.confirm).toBe(true);
    expect(w.type).toBe("Injury or medical");
    expect(w.eventIds.length).toBeGreaterThan(0);
    expect(w.author).toContain("Written by hand");
    const el = loaded.editions.el;
    expect(why(loaded.run, el.meta, el.data.sentences[0], 0).scope).toContain("Gate-cleared only");
  });

  it("recomputes the sample's bundle hash and every edition, and catches a flipped byte", () => {
    const run = sampleRun();
    const dir = join(WEB, "runs", "sample-s1");
    const manifestBytes = readFileSync(join(dir, "manifest.json"));
    const manifest = JSON.parse(manifestBytes.toString("utf8")) as BundleManifest;
    const files: Record<string, { sha256: string; bytes: number }> = {};
    for (const name of Object.keys(manifest.artifacts)) {
      const bytes = readFileSync(join(dir, name));
      files[name] = { sha256: createHash("sha256").update(bytes).digest("hex"), bytes: bytes.length };
    }
    const computed = { manifest: createHash("sha256").update(manifestBytes).digest("hex"), files };
    const good = bundleCheck(run, manifest, computed);
    expect(good.ok).toBe(true);
    expect(good.rows.length).toBe(5);
    const flipped = { ...computed, files: { ...files, "edition-el.json": { ...files["edition-el.json"], sha256: "0".repeat(64) } } };
    const bad = bundleCheck(run, manifest, flipped);
    expect(bad.ok).toBe(false);
    expect(bad.rows.find((r) => r.name === "edition-el.json")?.status).toBe("mismatch");
  });
});

/* The export of tests/recorded_kit.py by agon.webrecord, committed under
   tests/fixtures/recorded-run and held byte for byte by tests/test_webrecord.py:
   what the site shows once a real recorded run is the default. */
describe("the recorded run written by agon.webrecord", () => {
  const runsDir = join(RECORDED, "runs");
  const row = readJson<{ runs: { run_id: string; file: string; provenance: string }[] }>(join(runsDir, "index.json")).runs[0];
  const recorded = (): Run => readJson<Run>(join(runsDir, row.file));

  it("passes the browser's index and run rules", () => {
    expect(validateIndex(readJson(join(runsDir, "index.json")))).toEqual([]);
    expect(row.provenance).toBe("pipeline");
    expect(validateRun(recorded())).toEqual([]);
  });

  it("carries the approved label because its approval names its own bundle hash", () => {
    const run = recorded();
    expect(run.label).toBe("Recorded run, 2026-10-21, commit 0123456");
    expect(isApproved(run)).toBe(true);
    expect(editionLabel(run)).toBe(
      "AI-drafted. Numbers and names checked by code. Headline report and flagged sentences approved by the club media office at 18:05 UTC on 2026-10-21.",
    );
    const other = clone(run);
    other.approval.approved_bundle_sha256 = "0".repeat(64);
    expect(isApproved(other)).toBe(false);
  });

  it("tells the visitor the decision was already taken, and on which channel", () => {
    expect(decidedMessage(recorded())).toBe(
      "This report was already approved on Microsoft Teams by the approver at 18:05 UTC on 2026-10-21 and published, " +
        "for bundle afc16bdd87a7. This stand-in card sends nothing more.",
    );
    expect(decidedMessage(sampleRun())).toBeNull();
  });

  it("recomputes the bundle hash and every published file, and reports the unticked payloads as withheld", () => {
    const run = recorded();
    const dir = join(runsDir, run.bundle.dir);
    const manifestBytes = readFileSync(join(dir, "manifest.json"));
    const manifest = JSON.parse(manifestBytes.toString("utf8")) as BundleManifest;
    const withheld = withheldPayloads(run);
    expect([...withheld].sort()).toEqual(["destination-journalist_email.json", "destination-youtube.json"]);
    const files: Record<string, { sha256: string; bytes: number }> = {};
    for (const name of Object.keys(manifest.artifacts)) {
      if (withheld.has(name)) {
        continue;
      }
      const bytes = readFileSync(join(dir, name));
      files[name] = { sha256: createHash("sha256").update(bytes).digest("hex"), bytes: bytes.length };
    }
    const computed = { manifest: createHash("sha256").update(manifestBytes).digest("hex"), files };
    expect(computed.manifest).toBe(run.bundle.sha256);
    const good = bundleCheck(run, manifest, computed);
    expect(good.ok).toBe(true);
    expect(good.rows.filter((r) => r.status === "withheld").map((r) => r.name).sort()).toEqual([...withheld].sort());
    expect(good.rows.filter((r) => r.status === "match").length).toBe(Object.keys(files).length);
    expect(compareApproved(run, computed.manifest).kind).toBe("equal");
    /* A ticked payload that went missing is an error, never withheld. */
    const missing = clone(computed);
    delete missing.files["destination-club_site.json"];
    const gone = bundleCheck(run, manifest, missing);
    expect(gone.ok).toBe(false);
    expect(gone.rows.find((r) => r.name === "destination-club_site.json")?.status).toBe("error");
  });

  it("drives the destination chips from the run, in the statuses the Publisher left", () => {
    const run = recorded();
    expect(validateDestinations(run.destinations, true, String(run.state))).toEqual([]);
    expect(Object.fromEntries((run.destinations as Destination[]).map((d) => [d.id, d.status]))).toEqual({
      club_site: "delivered",
      feeds: "delivered",
      instagram: "preview_not_posted",
      youtube: "not_selected",
      newsroom_teams: "preview_not_posted",
      journalist_email: "not_selected",
    });
    expect(chipsFor(run).fromRun).toBe(true);
  });

  it("explains a translated sentence by its English id, s2-el by s2", async () => {
    const data = await loadRunsData(diskFetchAt(RECORDED));
    expect(data.defaultRunId).toBe(row.run_id);
    const loaded = data.runs[data.defaultRunId];
    const el = loaded.editions.el;
    const w = why(loaded.run, el.meta, el.data.sentences[1], 1);
    expect(w.sentenceId).toBe("s2-el");
    expect(w.type).toBe("Discipline or referee");
    expect(w.scope).toContain("Gate-cleared only");
    const en = loaded.editions.en;
    const headline = why(loaded.run, en.meta, en.data.sentences[1], 1);
    expect(headline.confirm).toBe(true);
    expect(headline.scope).toContain("Confirm row");
  });
});

describe("Blow full time", () => {
  it("opens the hand-written sample while no recorded run is listed", async () => {
    const committed = readJson<{ format: string; runs: { provenance: string }[] }>(join(WEB, "runs", "index.json"));
    const onlySample: FetchText = async (url) =>
      url === "runs/index.json"
        ? { ok: true, status: 200, text: JSON.stringify({ ...committed, runs: committed.runs.filter((r) => r.provenance === "design_preview") }) }
        : diskFetch(url);
    const data = await loadRunsData(onlySample);
    expect(runForBlow(data, 1)?.run.run_id).toBe("sample-s1");
    expect(runForBlow(data, 3)?.run.run_id).toBe("sample-s1");
  });

  it("opens the recorded run once one is listed, for its seed and for any other seed", async () => {
    const data = await loadRunsData(afterFetch);
    expect(data.defaultRunId).toBe("run-afc16bdd87a7");
    expect(runForBlow(data, 1)?.run.run_id).toBe("run-afc16bdd87a7");
    expect(runForBlow(data, 3)?.run.run_id).toBe("run-afc16bdd87a7");
    expect(Object.keys(data.runs)).toContain("sample-s1");
  });
});
