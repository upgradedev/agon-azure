/*
  Run rules for the Newsroom Wall, the stand-in approval card, the recap with
  its Why drawer and the run bundle check on Verify.

  Pure functions, no DOM. validateRun applies the same rules and the same
  problem wording as agon.webrun.validate_run, and tests/runs.test.ts holds it
  to the shared case list tests/js/run_cases.json at the repository root.

  One addition the Python side does not have yet: an optional `destinations`
  key (owner decision 2026-10-19). A run without it is checked exactly as
  before; a run with it must carry a well-formed list. agon.webrun still
  refuses the key, so no run can ship with it until the pipeline side accepts
  it; the stricter side wins at deploy time.
*/
import type {
  BundleArtifact,
  BundleManifest,
  BundleState,
  ConfirmRow,
  DestinationId,
  DestinationStatus,
  EditionMeta,
  GateVerdict,
  LoadedRun,
  Run,
  Sentence,
  SentenceType,
} from "./types";
import { isInt, isObject } from "./core";

export const RUNS_BASE = "runs/";
export const INDEX_NAME = "index.json";
export const RUN_FORMAT = "agon.web-run/1";
export const INDEX_FORMAT = "agon.web-runs/1";
export const BUNDLE_MANIFEST = "manifest.json";

/* Pinned with agon.webrun.DESIGN_PREVIEW_LABEL, ci/web_check.py and
   ci/readiness.json; tests fail if they drift. */
export const DESIGN_PREVIEW_LABEL = "Design preview: sample run written by hand, not produced by the pipeline.";
export const STANDIN_HEADER =
  "Stand-in for the Microsoft Teams card. It calls the same decision endpoint. The real Teams card is in the video.";
export const APPROVED_LABEL_PREFIX =
  "AI-drafted. Numbers and names checked by code. Headline report and flagged sentences approved by the club media office at ";
export const NOT_DEPLOYED_SENTENCE =
  "The decision endpoint is not deployed yet, so Approve and Request changes on this stand-in card explain what they would send instead of sending it.";

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
const EDITION_FILE = /^edition-[a-z]{2,8}\.json$/;
const SENTENCE_ID = /^[a-z]{2,8}-[0-9]{2}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const ISO_TIME = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?Z$/;
const RUN_KEYS = [
  "approval", "away", "bundle", "confirm_rows", "desk_editor_flags", "desks", "editions", "format",
  "gates", "generator", "handoffs", "home", "label", "match_id", "provenance", "run_id", "score", "seed",
  "sentence_gates", "sentence_types", "state", "states",
];
const OPTIONAL_RUN_KEYS = ["destinations"];

export const DESTINATION_IDS: DestinationId[] = ["club_site", "feeds", "instagram", "youtube", "newsroom_teams", "journalist_email"];
const DESTINATION_STATUSES: DestinationStatus[] = ["selected", "not_selected", "published", "sent", "not_posted"];
/* Destinations that are previews only: nothing is ever posted to them. */
export const PREVIEW_ONLY: DestinationId[] = ["instagram", "youtube"];

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

/* The optional destinations list: known ids, once each, a label, a known
   status, and a preview flag. A preview-only destination (Instagram, YouTube)
   must be marked preview and can never claim it was posted or sent. */
export function validateDestinations(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0) {
    return ["destinations must be a non-empty list when present"];
  }
  const problems: string[] = [];
  const seen = new Set<string>();
  value.forEach((row: unknown, i: number) => {
    const where = `destinations[${i}]`;
    if (!keysAre(row, ["id", "label", "preview", "status"])) {
      problems.push(`${where} keys must be exactly id, label, preview and status`);
      return;
    }
    const id = row.id;
    if (typeof id !== "string" || !DESTINATION_IDS.includes(id as DestinationId)) {
      problems.push(`${where}.id must be one of ${DESTINATION_IDS.join(", ")}`);
    } else if (seen.has(id)) {
      problems.push(`${where}.id ${id} is listed twice`);
    } else {
      seen.add(id);
    }
    if (!nonEmpty(row.label)) {
      problems.push(`${where}.label must be a non-empty string`);
    }
    if (typeof row.status !== "string" || !DESTINATION_STATUSES.includes(row.status as DestinationStatus)) {
      problems.push(`${where}.status must be one of ${DESTINATION_STATUSES.join(", ")}`);
    }
    if (typeof row.preview !== "boolean") {
      problems.push(`${where}.preview must be true or false`);
    }
    if (typeof id === "string" && PREVIEW_ONLY.includes(id as DestinationId)) {
      if (row.preview !== true) {
        problems.push(`${where} ${id} is a preview-only destination and must be marked preview`);
      }
      if (row.status === "published" || row.status === "sent") {
        problems.push(`${where} ${id} is a preview-only destination and can never be ${String(row.status)}`);
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
      if (typeof e.file !== "string" || !EDITION_FILE.test(e.file) || e.file !== `edition-${String(e.edition)}.json`) {
        problems.push(`editions[${i}].file must be edition-<edition>.json`);
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
    problems.push(...validateDestinations(run.destinations));
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
  if (name !== BUNDLE_MANIFEST && !EDITION_FILE.test(String(name))) {
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
    return "No destination is ticked, so even a deployed endpoint would have published to none of the club's channels.";
  }
  return `It would have gone to ${labels.length === 1 ? "one destination" : `${labels.length} destinations`}: ${joinLabels(labels)}. None of them received anything.`;
}

export function notDeployedMessage(action: "approve" | "changes", run: Run, destinationLabels: string[] = []): string {
  const hash = shortHash(run.bundle.sha256);
  if (action === "approve") {
    return (
      "Nothing was approved or published. The decision endpoint is not deployed yet, so this stand-in card " +
      `could not send your approval of bundle ${hash}. Once the decision endpoint is deployed, Approve sends ` +
      `that exact hash to the same endpoint the Teams card calls. ${destinationsSentence(destinationLabels)}`
    );
  }
  return (
    "Nothing was sent. The decision endpoint is not deployed yet, so this stand-in card could not send a " +
    `change request for bundle ${hash}. Once the decision endpoint is deployed, Request changes goes to ` +
    "the same endpoint the Teams card calls."
  );
}

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
  const headline = scope.attested.includes("edition-en.json");
  const flagged = run.confirm_rows.length;
  const parts: string[] = [];
  if (headline) {
    parts.push("headline report");
  }
  parts.push(`${flagged} flagged sentence${flagged === 1 ? "" : "s"}`);
  const cleared = scope.gate_cleared.length;
  return {
    attesting: `You are attesting: ${parts.join(" and ")}`,
    cleared: `Gate-cleared only: ${cleared} edition${cleared === 1 ? "" : "s"}`,
    flagged,
    clearedCount: cleared,
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
  const enIds = Object.keys(run.sentence_types).sort();
  const type = run.sentence_types[enIds[index] ?? ""];
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

export function matchLine(run: Run): string {
  return `${run.home.name} ${run.score.home}, ${run.away.name} ${run.score.away}. Synthetic match, seed ${run.seed}, fictional clubs and players.`;
}

/* ------------------------------------------------------------- bundle check */

export interface BundleRow {
  name: string;
  expected: string;
  computed: string;
  status: "match" | "mismatch" | "error";
  reason: string;
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
  const rows: BundleRow[] = Object.keys(listed)
    .sort()
    .map((name) => {
      const entry = listed[name];
      const got = computed.files[name];
      let status: BundleRow["status"] = "error";
      let reason = "";
      if (!got) {
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
  const filesMatch = rows.length > 0 && rows.every((r) => r.status === "match");
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
