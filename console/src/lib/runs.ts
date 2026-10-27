/*
  Run rules for the Newsroom Wall, the stand-in approval card, the recap with
  its Why drawer and the run bundle check on Verify.

  Pure functions, no DOM. validateRun applies the same rules and the same
  problem wording as agon.webrun.validate_run, and tests/runs.test.ts holds it
  to the shared case list tests/js/run_cases.json at the repository root.

  The optional `destinations` key follows agon.publish.webview (documented in
  web/runs/README.txt): validateDestinations ports its destination_problems
  rule by rule, with the same wording. The optional `check_flags` key holds
  the flags of every step but the Desk Editor, each naming its step;
  flagGroups groups them for the approval card.
*/
import type {
  AnalystNotes,
  AnalystPick,
  BundleArtifact,
  BundleManifest,
  BundleState,
  ConfirmRow,
  DeliveryKind,
  DeskEditorFlag,
  DestinationId,
  DestinationStatus,
  EditionMeta,
  GateVerdict,
  LoadedRun,
  Run,
  RunsData,
  Sentence,
  SentenceType,
  TurningPointTheme,
} from "./types";
import { isInt, isObject } from "./core";

export const RUNS_BASE = "runs/";

/* The run Blow full time opens for a seed. A recorded pipeline run comes first: the one for this
   seed, else the first one listed (the seed notice then says it is for another seed). Only while
   no recorded run is listed does the hand-written sample open, for this seed or as the default. */
export function runForBlow(data: RunsData, seed: number | null): LoadedRun | null {
  const all = data.index.runs.map((row) => data.runs[row.run_id]).filter((r): r is LoadedRun => Boolean(r));
  const recorded = all.filter((r) => r.run.provenance === "pipeline");
  return (
    recorded.find((r) => r.run.seed === seed) ??
    recorded[0] ??
    all.find((r) => r.run.seed === seed) ??
    data.runs[data.defaultRunId] ??
    null
  );
}
export const INDEX_NAME = "index.json";
export const RUN_FORMAT = "agon.web-run/1";
export const INDEX_FORMAT = "agon.web-runs/1";
export const BUNDLE_MANIFEST = "manifest.json";

/* Pinned with agon.webrun.DESIGN_PREVIEW_LABEL, ci/web_check.py and
   ci/readiness.json; tests fail if they drift. */
export const DESIGN_PREVIEW_LABEL = "Design preview: sample run written by hand, not produced by the pipeline.";
export const STANDIN_HEADER =
  "Stand-in for the Microsoft Teams card. In this public build it sends no decision; the real card is posted in the Microsoft Teams channel of the club media office.";
export const APPROVED_LABEL_PREFIX =
  "AI-drafted. Numbers and names checked by code. Headline report and flagged sentences approved by the club media office at ";
/* The stand-in card has no decision channel; the Teams card's decisions reach the one verifier
   through a queue and the decision intake job (Ran in Azure for the recorded run). */
export const NOT_DEPLOYED_SENTENCE =
  "This public page has no decision channel of its own, so Approve and Request changes on this stand-in card explain what they would send instead of sending it. Decisions on the Teams card reach the verifier through a queue and the decision intake job.";

export const STATES: BundleState[] = ["DRAFT", "CHECKED", "SEALED", "AWAITING_APPROVAL", "APPROVED", "PUBLISHED", "CHANGES_REQUESTED"];
/* The four states the wall walks through before a person decides. */
export const WALL_STATES: BundleState[] = ["DRAFT", "CHECKED", "SEALED", "AWAITING_APPROVAL"];
const STATE_WORDS: Record<BundleState, string> = {
  DRAFT: "Draft",
  CHECKED: "Checked",
  SEALED: "Sealed",
  AWAITING_APPROVAL: "Awaiting approval",
  APPROVED: "Approved",
  PUBLISHED: "Published",
  CHANGES_REQUESTED: "Changes requested",
};
const APPROVAL_STATUSES = ["awaiting", "approved", "changes_requested"];
const SENTENCE_TYPES: SentenceType[] = [
  "match_stat",
  "injury_medical",
  "disciplinary_referee",
  "quote_attribution",
  "transfer_contract",
  "neutral_colour",
];
export const CONFIRM_TYPES: Partial<Record<SentenceType, string>> = {
  injury_medical: "the injury sentence",
  disciplinary_referee: "the referee sentence",
  quote_attribution: "the quote sentence",
  transfer_contract: "the transfer sentence",
};
const TYPE_WORDS: Record<SentenceType, string> = {
  match_stat: "Match statistic",
  injury_medical: "Injury or medical",
  disciplinary_referee: "Discipline or referee",
  quote_attribution: "Quote",
  transfer_contract: "Transfer or contract",
  neutral_colour: "Colour",
};
const CLAIM_WORDS: Record<string, string> = { score: "Score", stat: "Statistic", minute: "Minute", name: "Name", event_ref: "Event" };
const AUTHOR_WORDS: Record<string, string> = {
  hand: "Written by hand for this design preview. No model wrote it.",
  model: "Written by a Microsoft Foundry agent.",
  template: "A template line written by code.",
  officer: "Edited by the club media officer.",
};
const GATE_VERDICTS: GateVerdict[] = ["pass", "mismatch", "refused", "skipped"];
export const VERDICT_WORDS: Record<GateVerdict, string> = { pass: "Pass", mismatch: "Mismatch", refused: "Refused", skipped: "Skipped" };
const GATE_WORDS: Record<string, string> = { factgate: "Fact Gate", factgate_lang: "Fact Gate, translation", rights: "Rights Guard" };

const RUN_ID = /^[a-z0-9][a-z0-9-]{0,62}$/;
/* A design preview names its editions edition-<id>.json; a pipeline run keeps
   the sealed bundle as published, whose editions are report-<id>.json, and its
   sentence ids look like s1, s1-el and s1-easy (agon.fleet.steps). */
const EDITION_FILE = /^(?:edition|report)-[a-z]{2,8}\.json$/;
const SENTENCE_ID = /^[a-z0-9][a-z0-9-]{0,62}$/;
/* Any file inside a published bundle folder: a plain artifact name, never a path. */
const ARTIFACT_FILE = /^[a-z0-9][a-z0-9._-]{0,94}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const ISO_TIME = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?Z$/;
const RUN_KEYS = [
  "approval", "away", "bundle", "confirm_rows", "desk_editor_flags", "desks", "editions", "format",
  "gates", "generator", "handoffs", "home", "label", "match_id", "provenance", "run_id", "score", "seed",
  "sentence_gates", "sentence_types", "state", "states",
];
/* check_flags: the card's flags on a sentence raised by any step but the Desk Editor
   (agon.webrecord.flags_by_source); a recorded run carries it, the sample does not. */
const OPTIONAL_RUN_KEYS = ["destinations", "check_flags"];
/* card.json "by": the step that raised a flag (agon.fleet.steps.add_flag). */
const FLAG_SOURCE = /^[a-z][a-z0-9_]{0,62}$/;

/* The destination registry (agon.governance.destinations): ids in order, their
   text labels, and how each one receives the approved bytes in this build. */
export const DESTINATION_LABELS: [DestinationId, string][] = [
  ["club_site", "Club website (CMS feed)"],
  ["feeds", "RSS and JSON Feed"],
  ["instagram", "Instagram"],
  ["youtube", "YouTube"],
  ["newsroom_teams", "Internal newsroom (Teams)"],
  ["journalist_email", "Journalist email digest"],
];
export const DESTINATION_IDS: DestinationId[] = DESTINATION_LABELS.map(([id]) => id);
export const DELIVERY: Record<DestinationId, DeliveryKind> = {
  club_site: "written",
  feeds: "written",
  instagram: "preview",
  youtube: "preview",
  newsroom_teams: "outbox",
  journalist_email: "outbox",
};
/* Destinations that are previews only: nothing is ever posted to them. */
export const PREVIEW_ONLY: DestinationId[] = ["instagram", "youtube"];
const DESTINATION_STATUSES: DestinationStatus[] = ["delivered", "preview_not_posted", "not_selected", "not_offered"];
/* The notes agon.publish.webview requires, verbatim. */
export const PREVIEW_LABEL = "Preview: not posted; posting needs Meta and Google app approval.";
export const OUTBOX_LABEL = "Not sent yet: the payload and its queue message are recorded; no code path sends them yet.";
export const WAITING_NOTE = "Approved; not written yet: the Publisher job has not published this bundle.";
const PREVIEW_KEYS = ["kind", "note", "path", "payload_sha256", "text", "title"];

function has(obj: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function nonEmpty(value: unknown): value is string {
  return typeof value === "string" && value.replace(/\s+/g, "") !== "";
}

function keysAre(obj: unknown, keys: string[]): obj is Record<string, unknown> {
  if (!isObject(obj)) {
    return false;
  }
  const actual = Object.keys(obj).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, i) => key === expected[i]);
}

/* ------------------------------------------------------------- validation */

export function validateIndex(index: unknown): string[] {
  if (!keysAre(index, ["format", "runs"])) {
    return ["runs index keys must be exactly format and runs"];
  }
  const problems: string[] = [];
  if (index.format !== INDEX_FORMAT) {
    problems.push(`runs index format must be ${INDEX_FORMAT}`);
  }
  const rows = index.runs;
  if (!Array.isArray(rows) || rows.length === 0) {
    problems.push("runs index must list at least one run");
    return problems;
  }
  const seen = new Set<string>();
  rows.forEach((row: unknown, i: number) => {
    const where = `runs[${i}]`;
    if (!keysAre(row, ["file", "provenance", "run_id"])) {
      problems.push(`${where} keys must be exactly file, provenance and run_id`);
      return;
    }
    if (typeof row.run_id !== "string" || !RUN_ID.test(row.run_id)) {
      problems.push(`${where}.run_id must be lowercase letters, digits and hyphens`);
    } else if (seen.has(row.run_id)) {
      problems.push(`${where}.run_id ${row.run_id} is listed twice`);
    } else {
      seen.add(row.run_id);
      if (row.file !== `${row.run_id}.json`) {
        problems.push(`${where}.file must be ${row.run_id}.json`);
      }
    }
    if (row.provenance !== "design_preview" && row.provenance !== "pipeline") {
      problems.push(`${where}.provenance must be design_preview or pipeline`);
    }
  });
  return problems;
}

/* The optional destinations key, with the rules and the wording of
   agon.publish.webview.destination_problems: all six destinations in registry
   order with their labels, a known status, a preview exactly when offered, and
   on a run that is not approved nothing delivered or previewed. */
export function validateDestinations(rows: unknown, approved: boolean, state: string): string[] {
  if (!Array.isArray(rows) || rows.length !== DESTINATION_IDS.length) {
    return [`destinations must list all ${DESTINATION_IDS.length} destinations`];
  }
  const problems: string[] = [];
  rows.forEach((row: unknown, i: number) => {
    const where = `destinations[${i}]`;
    const destId = DESTINATION_IDS[i];
    if (!keysAre(row, ["id", "label", "preview", "status"])) {
      problems.push(`${where} keys must be exactly id, label, preview and status`);
      return;
    }
    if (row.id !== destId) {
      problems.push(`${where}.id must be ${destId} (registry order)`);
      return;
    }
    const label = DESTINATION_LABELS[i][1];
    if (row.label !== label) {
      problems.push(`${where}.label must be '${label}'`);
    }
    const status = row.status;
    const preview = row.preview;
    if (typeof status !== "string" || !DESTINATION_STATUSES.includes(status as DestinationStatus)) {
      problems.push(`${where}.status must be one of ${DESTINATION_STATUSES.join(", ")}`);
      return;
    }
    if ((status === "not_offered") !== (preview === null)) {
      problems.push(`${where}: preview is null exactly when the status is not_offered`);
      return;
    }
    if (!approved && status !== "not_offered" && status !== "not_selected") {
      problems.push(`${where}: a run that is not approved has no delivered or previewed destination`);
    }
    if (preview === null) {
      return;
    }
    if (!keysAre(preview, PREVIEW_KEYS)) {
      problems.push(`${where}.preview keys must be exactly kind, note, path, payload_sha256, text and title`);
      return;
    }
    const kind = preview.kind;
    if (kind !== DELIVERY[destId]) {
      problems.push(`${where}.preview.kind must be ${DELIVERY[destId]}`);
    }
    if (typeof preview.payload_sha256 !== "string" || !SHA256_HEX.test(preview.payload_sha256)) {
      problems.push(`${where}.preview.payload_sha256 must be 64 lowercase hex characters`);
    }
    if (typeof preview.title !== "string" || typeof preview.text !== "string") {
      problems.push(`${where}.preview title and text must be strings`);
    }
    if (status === "delivered" && (kind !== "written" || state !== "PUBLISHED" || typeof preview.path !== "string")) {
      problems.push(`${where}: delivered means written bytes on a PUBLISHED run, with their path`);
    }
    if (status === "preview_not_posted") {
      const expected = kind === "preview" ? PREVIEW_LABEL : kind === "outbox" ? OUTBOX_LABEL : WAITING_NOTE;
      if (preview.note !== expected) {
        problems.push(`${where}.preview.note must say '${expected}'`);
      }
    }
  });
  return problems;
}

/* Same rules and the same problem wording as agon.webrun.validate_run. */
export function validateRun(run: unknown): string[] {
  if (!isObject(run)) {
    return ["run must be a JSON object"];
  }
  const missing = RUN_KEYS.filter((k) => !has(run, k));
  const extra = Object.keys(run)
    .filter((k) => !RUN_KEYS.includes(k) && !OPTIONAL_RUN_KEYS.includes(k))
    .sort();
  if (missing.length || extra.length) {
    return [`run keys differ: missing ${missing.join(", ") || "none"}; unexpected ${extra.join(", ") || "none"}`];
  }
  const problems: string[] = [];
  if (run.format !== RUN_FORMAT) {
    problems.push(`run format must be ${RUN_FORMAT}`);
  }
  if (typeof run.run_id !== "string" || !RUN_ID.test(run.run_id)) {
    problems.push("run_id must be lowercase letters, digits and hyphens");
  }
  const provenance = run.provenance;
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
  for (const key of ["match_id", "generator"]) {
    if (!nonEmpty(run[key])) {
      problems.push(`${key} must be a non-empty string`);
    }
  }
  for (const side of ["home", "away"]) {
    const value = run[side];
    if (!keysAre(value, ["club_id", "name"]) || !nonEmpty(value.name)) {
      problems.push(`${side} must be {club_id, name} with a name`);
    }
  }
  const score = run.score;
  if (!keysAre(score, ["away", "home"]) || !isInt(score.home) || !isInt(score.away) || score.home < 0 || score.away < 0) {
    problems.push("score must be {home, away} with non-negative integers");
  }
  const bundle = run.bundle;
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
      problems.push(`bundle.manifest must be ${BUNDLE_MANIFEST}`);
    }
  }
  if (!STATES.includes(run.state as BundleState)) {
    problems.push(`state must be one of ${STATES.join(", ")}`);
  }
  const states = run.states;
  if (!Array.isArray(states) || states.length === 0) {
    problems.push("states must be a non-empty list");
  } else {
    const steps: number[] = [];
    states.forEach((row: unknown, i: number) => {
      if (!keysAre(row, ["state", "step"]) || !STATES.includes(row.state as BundleState) || !isInt(row.step)) {
        problems.push(`states[${i}] must be {state, step} with a known state`);
      } else {
        steps.push(row.step);
      }
    });
    for (let s = 1; s < steps.length; s += 1) {
      if (steps[s] < steps[s - 1]) {
        problems.push("states must be in step order");
        break;
      }
    }
    const last: unknown = states[states.length - 1];
    if (isObject(last) && last.state !== run.state) {
      problems.push("the last entry in states must be the current state");
    }
  }
  const deskIds = new Set<string>();
  const desks = run.desks;
  if (!Array.isArray(desks) || desks.length === 0) {
    problems.push("desks must be a non-empty list");
  } else {
    desks.forEach((desk: unknown, i: number) => {
      if (!keysAre(desk, ["desk", "lines", "name"]) || !nonEmpty(desk.name)) {
        problems.push(`desks[${i}] must be {desk, name, lines}`);
        return;
      }
      deskIds.add(String(desk.desk));
      const lines = desk.lines;
      const ok =
        Array.isArray(lines) &&
        lines.length > 0 &&
        lines.every((line: unknown) => keysAre(line, ["step", "text"]) && isInt(line.step) && nonEmpty(line.text));
      if (!ok) {
        problems.push(`desks[${i}].lines must be a non-empty list of {step, text}`);
      }
    });
  }
  const handoffs = run.handoffs;
  if (!Array.isArray(handoffs)) {
    problems.push("handoffs must be a list");
  } else {
    handoffs.forEach((h: unknown, i: number) => {
      if (!keysAre(h, ["from", "step", "text", "to"]) || !isInt(h.step) || !nonEmpty(h.text)) {
        problems.push(`handoffs[${i}] must be {step, from, to, text}`);
      } else if (!deskIds.has(String(h.from)) || (!deskIds.has(String(h.to)) && h.to !== "approval")) {
        problems.push(`handoffs[${i}] names a desk that is not on the wall`);
      }
    });
  }
  const gates = run.gates;
  if (!Array.isArray(gates) || gates.length === 0) {
    problems.push("gates must be a non-empty list");
  } else {
    gates.forEach((g: unknown, i: number) => {
      if (!keysAre(g, ["detail", "evidence", "gate", "name", "verdict"]) || !GATE_VERDICTS.includes(g.verdict as GateVerdict)) {
        problems.push(`gates[${i}] must be {gate, name, verdict, detail, evidence} with a known verdict`);
      }
    });
  }
  const editionIds = new Set<string>();
  const editions = run.editions;
  if (!Array.isArray(editions) || editions.length === 0) {
    problems.push("editions must be a non-empty list");
  } else {
    editions.forEach((e: unknown, i: number) => {
      if (!keysAre(e, ["edition", "file", "language", "name"])) {
        problems.push(`editions[${i}] keys must be exactly edition, file, language and name`);
        return;
      }
      const allowed = [`edition-${String(e.edition)}.json`];
      if (run.provenance === "pipeline") {
        allowed.push(`report-${String(e.edition)}.json`);
      }
      if (typeof e.file !== "string" || !EDITION_FILE.test(e.file) || !allowed.includes(e.file)) {
        problems.push(`editions[${i}].file must be edition-<edition>.json${allowed.length > 1 ? " or report-<edition>.json" : ""}`);
      }
      const id = String(e.edition);
      if (editionIds.has(id)) {
        problems.push(`editions[${i}].edition ${id} is listed twice`);
      }
      editionIds.add(id);
    });
  }
  let types: Record<string, unknown> = {};
  if (!isObject(run.sentence_types) || Object.keys(run.sentence_types).length === 0) {
    problems.push("sentence_types must be a non-empty map");
  } else {
    types = run.sentence_types;
    for (const sid of Object.keys(types)) {
      if (!SENTENCE_ID.test(sid) || !SENTENCE_TYPES.includes(types[sid] as SentenceType)) {
        problems.push(`sentence_types[${sid}] must map a sentence id to a known type`);
      }
    }
  }
  const sentenceGates = run.sentence_gates;
  if (!isObject(sentenceGates)) {
    problems.push("sentence_gates must be a map");
  } else {
    for (const sid of Object.keys(sentenceGates)) {
      const v = sentenceGates[sid];
      const ok =
        SENTENCE_ID.test(sid) &&
        isObject(v) &&
        Object.keys(v).length > 0 &&
        Object.keys(v).every((g) => GATE_VERDICTS.includes(v[g] as GateVerdict));
      if (!ok) {
        problems.push(`sentence_gates[${sid}] must map gate names to known verdicts`);
      }
    }
  }
  const confirmRows = run.confirm_rows;
  if (!Array.isArray(confirmRows)) {
    problems.push("confirm_rows must be a list");
  } else {
    const listed = new Set<string>();
    confirmRows.forEach((row: unknown, i: number) => {
      if (!keysAre(row, ["label", "row_id", "sentence_id", "type"])) {
        problems.push(`confirm_rows[${i}] keys must be exactly label, row_id, sentence_id and type`);
        return;
      }
      const type = row.type as SentenceType;
      if (!has(CONFIRM_TYPES, String(row.type)) || row.label !== CONFIRM_TYPES[type]) {
        problems.push(`confirm_rows[${i}] must use the routing table's type and label`);
      }
      if (types[String(row.sentence_id)] !== row.type) {
        problems.push(`confirm_rows[${i}] does not match the type of ${String(row.sentence_id)}`);
      }
      listed.add(String(row.sentence_id));
    });
    const needed = Object.keys(types)
      .filter((sid) => has(CONFIRM_TYPES, String(types[sid])) && !listed.has(sid))
      .sort();
    if (needed.length) {
      problems.push(`every injury, referee, quote and transfer sentence needs a Confirm row: missing ${needed.join(", ")}`);
    }
  }
  const flags = run.desk_editor_flags;
  const flagsOk = Array.isArray(flags) && flags.every((f: unknown) => keysAre(f, ["sentence_id", "text"]) && nonEmpty(f.text));
  if (!flagsOk) {
    problems.push("desk_editor_flags must be a list of {sentence_id, text}");
  }
  if (has(run, "check_flags")) {
    const checks = run.check_flags;
    const checksOk =
      Array.isArray(checks) &&
      checks.every(
        (f: unknown) =>
          keysAre(f, ["by", "name", "sentence_id", "text"]) &&
          typeof f.sentence_id === "string" &&
          SENTENCE_ID.test(f.sentence_id) &&
          typeof f.by === "string" &&
          FLAG_SOURCE.test(f.by) &&
          f.by !== "desk_editor" &&
          nonEmpty(f.name) &&
          nonEmpty(f.text),
      );
    if (!checksOk) {
      problems.push("check_flags must be a list of {sentence_id, by, name, text}, none by the Desk Editor");
    }
  }
  const approval = run.approval;
  if (
    !keysAre(approval, ["approved_at", "approved_bundle_sha256", "approver_role", "channel", "status"]) ||
    !APPROVAL_STATUSES.includes(String(approval.status))
  ) {
    problems.push("approval must be {status, approved_at, approver_role, channel, approved_bundle_sha256} with a known status");
  } else {
    const approved = approval.status === "approved";
    if (approved !== (run.state === "APPROVED" || run.state === "PUBLISHED")) {
      problems.push("approval.status and state disagree");
    }
    if (
      approved &&
      (typeof approval.approved_at !== "string" ||
        !ISO_TIME.test(approval.approved_at) ||
        !nonEmpty(approval.approver_role) ||
        !nonEmpty(approval.channel))
    ) {
      problems.push("an approved run must name approved_at (UTC, ending in Z), approver_role and channel");
    }
    if (!approved && approval.approved_at !== null) {
      problems.push("a run that is not approved has no approved_at");
    }
    if (approved && provenance === "design_preview") {
      problems.push("a design preview run can never be approved");
    }
    const stated = isObject(bundle) ? bundle.sha256 : undefined;
    if (approved && (approval.approved_bundle_sha256 === null || approval.approved_bundle_sha256 !== stated)) {
      problems.push("an approved run must name the approved bundle: approved_bundle_sha256 must equal bundle.sha256");
    }
    if (!approved && approval.approved_bundle_sha256 !== null) {
      problems.push("a run that is not approved has no approved_bundle_sha256");
    }
  }
  if (has(run, "destinations")) {
    const approvedRun = isObject(approval) && approval.status === "approved";
    problems.push(...validateDestinations(run.destinations, approvedRun, String(run.state)));
  }
  return problems;
}

/* ------------------------------------------------------------- urls */

export function isSafeRunId(runId: unknown): runId is string {
  return typeof runId === "string" && RUN_ID.test(runId);
}

export function runUrl(runId: string): string {
  if (!isSafeRunId(runId)) {
    throw new Error("refusing an unsafe run id");
  }
  return `${RUNS_BASE}${runId}.json`;
}

export function bundleFileUrl(run: Run, name: string): string {
  const safe = run.provenance === "pipeline" ? ARTIFACT_FILE.test(String(name)) : EDITION_FILE.test(String(name));
  if (name !== BUNDLE_MANIFEST && !safe) {
    throw new Error("refusing an unsafe bundle file name");
  }
  return `${RUNS_BASE}${run.bundle.dir}/${name}`;
}

/* ------------------------------------------------------------- wall */

export function deskNames(run: Run): Record<string, string> {
  const names: Record<string, string> = { approval: "Approval card" };
  for (const d of run.desks) {
    names[d.desk] = d.name;
  }
  return names;
}

export type WallItem =
  | { kind: "line"; step: number; desk: string; deskName: string; text: string }
  | { kind: "handoff"; step: number; from: string; to: string; text: string }
  | { kind: "state"; step: number; state: BundleState; text: string };

/* One ordered list for the phone timeline: desk lines, handoffs and state
   changes by step; at one step a line comes before its handoff, and the
   handoff before the state it causes. */
export function wallItems(run: Run): WallItem[] {
  const names = deskNames(run);
  const order = { line: 0, handoff: 1, state: 2 };
  const items: WallItem[] = [];
  for (const d of run.desks) {
    for (const line of d.lines) {
      items.push({ kind: "line", step: line.step, desk: d.desk, deskName: d.name, text: line.text });
    }
  }
  for (const h of run.handoffs) {
    items.push({ kind: "handoff", step: h.step, from: names[h.from] ?? h.from, to: names[h.to] ?? h.to, text: h.text });
  }
  for (const s of run.states) {
    items.push({ kind: "state", step: s.step, state: s.state, text: `Bundle state: ${STATE_WORDS[s.state]}` });
  }
  return items.sort((a, b) => a.step - b.step || order[a.kind] - order[b.kind]);
}

export function lastStep(run: Run): number {
  return wallItems(run).reduce((max, item) => Math.max(max, item.step), 0);
}

/* The bundle state at a step of the replay, or "" before the first draft. */
export function stateAt(run: Run, step: number): BundleState | "" {
  let current: BundleState | "" = "";
  for (const s of run.states) {
    if (s.step <= step) {
      current = s.state;
    }
  }
  return current;
}

export function stateWords(state: BundleState | ""): string {
  return state ? STATE_WORDS[state] ?? state : "No bundle yet";
}

/* ------------------------------------------------------------- analyst */

/* A pipeline bundle seals the Analyst's turning points in this artifact. */
export const CARD_FILE = "card.json";
const THEMES: TurningPointTheme[] = ["control_vs_chaos", "tactical_pressure", "game_rhythm"];
/* The rules' terms, as the glossary names control vs chaos. */
export const THEME_WORDS: Record<TurningPointTheme, string> = {
  control_vs_chaos: "Control vs chaos",
  tactical_pressure: "Tactical pressure",
  game_rhythm: "Game rhythm",
};
const PICK_KEYS = ["author", "event_id", "minute", "theme", "why"];
const PICK_EVENT_ID = /^[A-Za-z0-9_-]{1,64}$/;
/* agon.governance.recompute.display_minute: 4, 81, 45+2, 90+1. */
const PICK_MINUTE = /^[0-9]{1,3}(?:\+[0-9]{1,2})?$/;
/* A deployment or code id as a sealed manifest names it: agon-analyst:2, agon.interpret/7ab5e12ab86e. */
const SOURCE_ID = /^[A-Za-z0-9][A-Za-z0-9:./_-]{0,63}$/;

/* The turning points in a sealed card.json, each exactly {author, event_id, minute, theme, why}
   as agon.fleet.steps.analyst writes it. One unexpected item and the list is empty, so a screen
   never shows a partial or unknown list. */
export function analystPicks(card: unknown): AnalystPick[] {
  const found = isObject(card) ? card.turning_points : null;
  if (!Array.isArray(found)) {
    return [];
  }
  const list: unknown[] = found;
  const picks: AnalystPick[] = [];
  for (const item of list) {
    if (!keysAre(item, PICK_KEYS)) {
      return [];
    }
    const { author, event_id: eventId, minute, theme, why: text } = item;
    if (
      (author !== "model" && author !== "template") ||
      typeof eventId !== "string" ||
      !PICK_EVENT_ID.test(eventId) ||
      typeof minute !== "string" ||
      !PICK_MINUTE.test(minute) ||
      typeof theme !== "string" ||
      !THEMES.includes(theme as TurningPointTheme) ||
      !nonEmpty(text)
    ) {
      return [];
    }
    picks.push({ author, event_id: eventId, minute, theme: theme as TurningPointTheme, why: text });
  }
  return picks;
}

/* The Analyst's notes for a run: its picks, the agent the sealed manifest names and the code
   that computed the candidate moments; null when the card carries no turning point. */
export function analystNotes(card: unknown, manifest: BundleManifest): AnalystNotes | null {
  const picks = analystPicks(card);
  if (!picks.length) {
    return null;
  }
  const ids = manifest.deployment_ids;
  const named = isObject(ids) ? ids["agent.analyst"] : null;
  const computed = isObject(card) ? card.interpret_source : null;
  return {
    picks,
    agent: typeof named === "string" && SOURCE_ID.test(named) ? named : null,
    interpretSource: typeof computed === "string" && SOURCE_ID.test(computed) ? computed : null,
  };
}

export function aiWritten(notes: AnalystNotes): boolean {
  return notes.picks.every((p) => p.author === "model");
}

export function pickAuthorWords(pick: AnalystPick): string {
  return pick.author === "model" ? "AI-written" : "Chosen by code";
}

/* What the Analyst did, in the order agon.fleet.workflow runs it: code computes the candidate
   moments (interpret), the agent reads them and picks events (analyst; code checks each pick is
   in the event log, not that it is a candidate), and the picks go into the Writer's brief
   (agon.fleet.templates.writer_brief). */
export function analystSummary(notes: AnalystNotes): string {
  const n = notes.picks.length;
  const points = `${n} turning point${n === 1 ? "" : "s"}`;
  if (aiWritten(notes)) {
    const by = notes.interpretSource ? ` (${notes.interpretSource})` : "";
    return `Before the Writer started, the Analyst agent read the candidate moments that code${by} computed from the event log, picked ${points}, and wrote a theme and why each one matters. The picks went to the Writer with the match facts.`;
  }
  /* agon.fleet.steps.analyst falls back when the agent was unavailable or neither of its two answers passed code's checks. */
  return `No pick from the agent was used, so before the Writer started, code took ${points} from the top of the candidate list. They went to the Writer with the match facts.`;
}

/* Who wrote the words, what code checked, and where they are sealed. */
export function analystSource(notes: AnalystNotes, run: Run): string {
  const sealed = `Sealed in ${CARD_FILE} in bundle ${shortHash(run.bundle.sha256)}; Verify recomputes its hash.`;
  if (!notes.picks.some((p) => p.author === "model")) {
    return `Chosen by code, not written by a model. ${sealed}`;
  }
  const agent = notes.agent ? `the Microsoft Foundry agent ${notes.agent}` : "a Microsoft Foundry agent";
  return (
    `Written by ${agent}. Code checked that each pick is an event in the log and read its minute from that event. ` +
    `The why and the theme are the agent's own words: the Fact Gate and the Rights Guard check the report's sentences, not this text. ${sealed}`
  );
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/* The agent sometimes names the clubs by id (C6's early breakthrough); this says which club each id is. */
export function clubKey(notes: AnalystNotes, run: Run): string | null {
  const sides = [run.home, run.away].filter((side) =>
    notes.picks.some((p) => new RegExp(`\\b${escapeRegExp(side.club_id)}\\b`).test(p.why)),
  );
  if (!sides.length) {
    return null;
  }
  return `The agent names the clubs by id: ${joinLabels(sides.map((side) => `${side.club_id} is ${side.name}`))}.`;
}

/* The picks whose event a sentence cites, for the Why drawer. */
export function picksCited(notes: AnalystNotes | null, eventIds: string[]): AnalystPick[] {
  if (!notes) {
    return [];
  }
  const cited = new Set(eventIds);
  return notes.picks.filter((p) => cited.has(p.event_id));
}

/* ------------------------------------------------------------- approval */

export function joinLabels(labels: string[]): string {
  if (labels.length <= 1) {
    return labels.join("");
  }
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

export function shortHash(hash: string): string {
  return String(hash).slice(0, 12);
}

export interface ApproveAttempt {
  ok: boolean;
  missing: string[];
  message: string;
}

/* Approve is never disabled. Clicking it either names every unticked Confirm
   row or, with all ticked, says in full why nothing was sent and which
   destinations would have received the approved bytes. */
export function approveAttempt(run: Run, tickedRowIds: string[], destinationLabels: string[] = []): ApproveAttempt {
  const ticked = new Set(tickedRowIds);
  const missing = run.confirm_rows.filter((row: ConfirmRow) => !ticked.has(row.row_id));
  if (missing.length) {
    return {
      ok: false,
      missing: missing.map((row) => row.row_id),
      message: `Tick ${joinLabels(missing.map((row) => row.label))} before approving.`,
    };
  }
  return { ok: true, missing: [], message: notDeployedMessage("approve", run, destinationLabels) };
}

export function destinationsSentence(labels: string[]): string {
  if (!labels.length) {
    return "No destination is ticked. If the bundle offers destinations, the verifier refuses an approval that ticks none of them; a bundle that offers none publishes to the club feed only.";
  }
  return `It would have gone to ${labels.length === 1 ? "one destination" : `${labels.length} destinations`}: ${joinLabels(labels)}. None of them received anything.`;
}

export function notDeployedMessage(action: "approve" | "changes", run: Run, destinationLabels: string[] = []): string {
  const hash = shortHash(run.bundle.sha256);
  if (action === "approve") {
    return (
      "Nothing was approved or published. This stand-in card has no decision channel, so it could not send your " +
      `approval of bundle ${hash}. On the Teams card, the same approval goes through a queue to the decision intake ` +
      `job, which checks it with the one verifier before anything is published. ${destinationsSentence(destinationLabels)}`
    );
  }
  return (
    "Nothing was sent. This stand-in card has no decision channel, so it could not send a change request for " +
    `bundle ${hash}. On the Teams card, a change request takes the same queue to the decision intake job.`
  );
}

/* On a run that is no longer waiting, the stand-in card says what was decided
   instead of pretending to decide again. */
export const CHANNEL_WORDS: Record<string, string> = {
  teams: "Microsoft Teams",
  outlook: "an Outlook approval email",
  signed_link: "a signed link",
  console: "the console",
};

export function decidedMessage(run: Run): string | null {
  const a = run.approval;
  if (a.status === "approved" && a.approved_at) {
    const published = run.state === "PUBLISHED" ? " and published" : "";
    const channel = CHANNEL_WORDS[a.channel ?? ""] ?? "an approval channel";
    return (
      `This report was already approved on ${channel} by the ${a.approver_role ?? "approver"} at ` +
      `${formatUtc(a.approved_at)}${published}, for bundle ${shortHash(run.bundle.sha256)}. This stand-in card sends nothing more.`
    );
  }
  if (a.status === "changes_requested") {
    return "The club media office already asked for changes on this report, so this stand-in card sends nothing more.";
  }
  return null;
}

/* The card's standing notice on a decided run, in place of the not-deployed sentence. */
export const DECIDED_CARD_SENTENCE =
  "This run is already decided, so Approve and Request changes on this stand-in card send nothing; they only say how it was decided.";

export interface Attestation {
  attesting: string;
  cleared: string;
  flagged: number;
  clearedCount: number;
}

/* "You are attesting" versus "Gate-cleared only", from the sealed manifest's
   attested scope and the run's Confirm rows. */
export function attestation(run: Run, manifest: BundleManifest): Attestation {
  const scope = manifest.attested_scope;
  const enFile = run.editions.find((e) => e.edition === "en")?.file ?? "edition-en.json";
  const headline = scope.attested.includes(enFile);
  const flagged = run.confirm_rows.length;
  const parts: string[] = [];
  if (headline) {
    parts.push("headline report");
  }
  parts.push(`${flagged} flagged sentence${flagged === 1 ? "" : "s"}`);
  /* The gate-cleared set of a pipeline bundle also holds audio, captions and the card file, so
     each kind is counted under its own name, never all of them as editions. */
  const names = scope.gate_cleared;
  const editions = names.filter((n) => EDITION_FILE.test(n)).length;
  const audio = names.filter((n) => n.startsWith("audio-")).length;
  const captions = names.filter((n) => n.startsWith("captions-")).length;
  const card = names.includes("card.json") ? 1 : 0;
  const other = names.length - editions - audio - captions - card;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const kinds = [plural(editions, "edition")];
  if (audio) {
    kinds.push(plural(audio, "audio file"));
  }
  if (captions) {
    kinds.push(plural(captions, "caption file"));
  }
  if (card) {
    kinds.push("the card file");
  }
  if (other) {
    kinds.push(plural(other, "other file"));
  }
  return {
    attesting: `You are attesting: ${parts.join(" and ")}`,
    cleared: `Gate-cleared only: ${joinLabels(kinds)}`,
    flagged,
    clearedCount: names.length,
  };
}

/* ------------------------------------------------------------- recap */

export function formatUtc(iso: string): string {
  const m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})/.exec(iso);
  if (!m) {
    return iso;
  }
  return `${m[4]}:${m[5]} UTC on ${m[1]}-${m[2]}-${m[3]}`;
}

/* Approved means a pipeline run whose approval names the very bundle hash the
   run states (the hash Verify recomputes). A status of "approved" on its own
   is never enough, even if a caller skipped validateRun. */
export function isApproved(run: unknown): boolean {
  if (!isObject(run) || run.provenance !== "pipeline" || !isObject(run.approval) || !isObject(run.bundle)) {
    return false;
  }
  const sha = run.bundle.sha256;
  return (
    run.approval.status === "approved" &&
    typeof sha === "string" &&
    SHA256_HEX.test(sha) &&
    run.approval.approved_bundle_sha256 === sha
  );
}

/* The edition label: the approved wording only on an approved pipeline run
   whose approval matches its bundle hash; everything else says what it is. */
export function editionLabel(run: Run): string {
  if (isApproved(run)) {
    return `${APPROVED_LABEL_PREFIX}${formatUtc(String(run.approval.approved_at))}.`;
  }
  if (run.provenance === "design_preview") {
    return `Not approved. ${DESIGN_PREVIEW_LABEL} No AI wrote these editions and no media officer has approved them.`;
  }
  if (run.approval.status === "approved") {
    return "AI-drafted. Numbers and names checked by code. Not approved: the approval on file does not name this bundle hash.";
  }
  if (run.approval.status === "changes_requested") {
    return "AI-drafted. Numbers and names checked by code. The club media office asked for changes, so this edition is not approved.";
  }
  return "AI-drafted. Numbers and names checked by code. Not yet approved by the club media office.";
}

export interface WhyGate {
  gate: string;
  name: string;
  verdict: GateVerdict;
  words: string;
}

export interface Why {
  sentenceId: string;
  text: string;
  eventIds: string[];
  claimTypes: string[];
  gates: WhyGate[];
  author: string;
  authorTag: string;
  type: string;
  scope: string;
  confirm: boolean;
}

function unique(list: string[]): string[] {
  return [...new Set(list)];
}

/* Everything the Why drawer lists for one sentence of one edition. Edition
   sentence n has the type of English sentence n. */
export function why(run: Run, edition: EditionMeta, sentence: Sentence, index: number): Why {
  /* The English sentence this one renders: itself, or for a pipeline edition the id without its
     edition suffix (s1-el is s1); the sample's editions follow the English order instead. */
  const suffix = `-${edition.edition}`;
  const base = sentence.sentence_id.endsWith(suffix) ? sentence.sentence_id.slice(0, -suffix.length) : "";
  const enIds = Object.keys(run.sentence_types).sort();
  const type =
    run.sentence_types[sentence.sentence_id] ?? (base ? run.sentence_types[base] : undefined) ?? run.sentence_types[enIds[index] ?? ""];
  const eventIds: string[] = [];
  const claimTypes: string[] = [];
  for (const claim of sentence.claims ?? []) {
    claimTypes.push(CLAIM_WORDS[claim.type] ?? claim.type);
    for (const id of claim.event_ids ?? []) {
      eventIds.push(id);
    }
  }
  const verdicts = run.sentence_gates[sentence.sentence_id] ?? {};
  const gates = Object.keys(verdicts)
    .sort()
    .map((g) => ({ gate: g, name: GATE_WORDS[g] ?? g, verdict: verdicts[g], words: VERDICT_WORDS[verdicts[g]] ?? verdicts[g] }));
  const headline = edition.edition === "en";
  const confirm = type !== undefined && has(CONFIRM_TYPES, type);
  let scope: string;
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
    gates,
    author: AUTHOR_WORDS[sentence.author] ?? `Author tag ${sentence.author}.`,
    authorTag: sentence.author,
    type: type ? TYPE_WORDS[type] : "Not typed",
    scope,
    confirm,
  };
}

/* The sentence with this id in any edition, for Confirm rows, flags and deep links. */
export function sentenceById(
  loaded: LoadedRun,
  sentenceId: string,
): { edition: string; index: number; sentence: Sentence } | null {
  for (const key of Object.keys(loaded.editions)) {
    const list = loaded.editions[key].data.sentences;
    const index = list.findIndex((s) => s.sentence_id === sentenceId);
    if (index !== -1) {
      return { edition: key, index, sentence: list[index] };
    }
  }
  return null;
}

export interface FlagGroup {
  /* The step that raised these flags, as the card shows it: "Fact Gate", "Desk Editor". */
  name: string;
  flags: DeskEditorFlag[];
}

/* The approval card's flags, each under the step that raised it: the other steps'
   flags (check_flags) grouped by step in the order the run lists them, then the
   Desk Editor's own. A Fact Gate flag is never shown as a Desk Editor flag. */
export function flagGroups(run: Run): FlagGroup[] {
  const groups: FlagGroup[] = [];
  for (const flag of run.check_flags ?? []) {
    const group = groups.find((g) => g.name === flag.name);
    const entry = { sentence_id: flag.sentence_id, text: flag.text };
    if (group) {
      group.flags.push(entry);
    } else {
      groups.push({ name: flag.name, flags: [entry] });
    }
  }
  if (run.desk_editor_flags.length) {
    groups.push({ name: "Desk Editor", flags: run.desk_editor_flags });
  }
  return groups;
}

export function matchLine(run: Run): string {
  return `${run.home.name} ${run.score.home}, ${run.away.name} ${run.score.away}. Synthetic match, seed ${run.seed}, fictional clubs and players.`;
}

/* ------------------------------------------------------------- bundle check */

export interface BundleRow {
  name: string;
  expected: string;
  computed: string;
  status: "match" | "mismatch" | "error" | "withheld";
  reason: string;
}

/* Payloads the Publisher never publishes: an offered destination the officer did not tick. */
export function withheldPayloads(run: Run): Set<string> {
  return new Set((run.destinations ?? []).filter((d) => d.status === "not_selected").map((d) => `destination-${d.id}.json`));
}

export interface BundleCheck {
  bundleMatches: boolean;
  filesMatch: boolean;
  ok: boolean;
  rows: BundleRow[];
}

export interface ComputedBundle {
  manifest: string;
  files: Record<string, { sha256: string; bytes: number }>;
}

/* Compare what the browser computed with what the run and manifest state. */
export function bundleCheck(run: Run, manifest: BundleManifest | null, computed: ComputedBundle): BundleCheck {
  const listed: Record<string, BundleArtifact> = manifest && isObject(manifest.artifacts) ? manifest.artifacts : {};
  const withheld = withheldPayloads(run);
  const rows: BundleRow[] = Object.keys(listed)
    .sort()
    .map((name) => {
      const entry = listed[name];
      const got = computed.files[name];
      let status: BundleRow["status"] = "error";
      let reason = "";
      if (!got && withheld.has(name)) {
        status = "withheld";
        reason = "the officer did not tick this destination, so the Publisher never published its payload; the manifest still names it by hash";
      } else if (!got) {
        reason = "the file could not be fetched";
      } else if (got.sha256 !== entry.sha256) {
        status = "mismatch";
        reason = "the bytes received hash to a different value";
      } else if (got.bytes !== entry.bytes) {
        status = "mismatch";
        reason = `the file has ${got.bytes} bytes, the manifest lists ${entry.bytes}`;
      } else {
        status = "match";
      }
      return { name, expected: entry.sha256, computed: got ? got.sha256 : "", status, reason };
    });
  const bundleMatches = computed.manifest === run.bundle.sha256;
  const filesMatch = rows.some((r) => r.status === "match") && rows.every((r) => r.status === "match" || r.status === "withheld");
  return { bundleMatches, filesMatch, ok: bundleMatches && filesMatch, rows };
}

/* "Approved hash equals published hash", computed from the run and the hash
   this browser recomputed, never asserted. */
export type ApprovalComparison =
  | { kind: "no-approval"; sentence: string }
  | { kind: "equal"; sentence: string }
  | { kind: "differs"; sentence: string };

export function compareApproved(run: Run, recomputedManifestHash: string): ApprovalComparison {
  const approved = run.approval.approved_bundle_sha256;
  if (approved === null) {
    const why =
      run.provenance === "design_preview"
        ? "this run is a design preview, which can never be approved"
        : "no media officer has approved this run yet";
    return {
      kind: "no-approval",
      sentence: `There is no approved hash to compare with the published one, because ${why}.`,
    };
  }
  if (approved === recomputedManifestHash) {
    return {
      kind: "equal",
      sentence: `The approved hash equals the published hash: both are ${shortHash(approved)}, and the second was computed on this page.`,
    };
  }
  return {
    kind: "differs",
    sentence: `The approved hash ${shortHash(approved)} does not equal the published hash ${shortHash(recomputedManifestHash || "none")} computed on this page.`,
  };
}
