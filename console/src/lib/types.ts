/*
  Types for the two static data contracts the console reads.

  Match data (web/data-fixture/README.txt, agon.webdata): manifest.json lists
  <seed>.ndjson event files and <seed>.world.json name files.

  Run data (web/runs/README.txt, agon.webrun): runs/index.json lists the runs,
  runs/<id>.json is the trace, runs/<id>/ holds the sealed bundle.

  The shapes are checked at run time by validateManifest, validateWorld,
  validateIndex and validateRun before any screen uses them; these types only
  describe what a value that passed looks like.
*/

export interface ManifestEntry {
  name: string;
  sha256: string;
  seed: number;
  events: number;
  world: string;
  world_sha256: string;
}

export interface DataManifest {
  generator: string;
  files: ManifestEntry[];
}

export type Position = "GK" | "DF" | "MF" | "FW";

export interface Player {
  player_id: string;
  name: string;
  shirt: number;
  position: Position;
}

export interface Club {
  club_id: string;
  name: string;
  short: string;
  kit_primary: string;
  kit_primary_hex: string;
  kit_secondary: string;
  kit_secondary_hex: string;
  players: Player[];
}

export interface World {
  generator: string;
  seed: number;
  match_id: string;
  league: string;
  home: string;
  away: string;
  clubs: Club[];
}

export interface MatchEvent {
  seq: number;
  period: number;
  minute: number;
  second: number;
  type: string;
  team: string;
  x_dm: number;
  y_dm: number;
  end_x_dm?: number | null;
  end_y_dm?: number | null;
  player?: string | null;
  related_player?: string | null;
  outcome?: string | null;
  qualifier?: string | null;
  under_pressure?: boolean;
  event_id?: string;
}

export type Provenance = "design_preview" | "pipeline";

export type BundleState =
  | "DRAFT"
  | "CHECKED"
  | "SEALED"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "PUBLISHED"
  | "CHANGES_REQUESTED";

export type GateVerdict = "pass" | "mismatch" | "refused" | "skipped";

export type SentenceType =
  | "match_stat"
  | "injury_medical"
  | "disciplinary_referee"
  | "quote_attribution"
  | "transfer_contract"
  | "neutral_colour";

export type ApprovalStatus = "awaiting" | "approved" | "changes_requested";

export interface RunsIndexRow {
  run_id: string;
  file: string;
  provenance: Provenance;
}

export interface RunsIndex {
  format: string;
  runs: RunsIndexRow[];
}

export interface StateStep {
  state: BundleState;
  step: number;
}

export interface DeskLine {
  step: number;
  text: string;
}

export interface Desk {
  desk: string;
  name: string;
  lines: DeskLine[];
}

export interface Handoff {
  step: number;
  from: string;
  to: string;
  text: string;
}

export interface Gate {
  gate: string;
  name: string;
  verdict: GateVerdict;
  detail: string;
  evidence: string;
}

export interface EditionMeta {
  edition: string;
  file: string;
  language: string;
  name: string;
}

export interface ConfirmRow {
  row_id: string;
  sentence_id: string;
  type: SentenceType;
  label: string;
}

export interface DeskEditorFlag {
  sentence_id: string;
  text: string;
}

/* A flag on a sentence raised by any step but the Desk Editor (card.json "by"), with that step named. */
export interface CheckFlag {
  sentence_id: string;
  by: string;
  name: string;
  text: string;
}

export interface Approval {
  status: ApprovalStatus;
  approved_at: string | null;
  approver_role: string | null;
  channel: string | null;
  approved_bundle_sha256: string | null;
}

export type DestinationId = "club_site" | "feeds" | "instagram" | "youtube" | "newsroom_teams" | "journalist_email";

/* The destinations key as agon.publish.webview writes it (web/runs/README.txt). */
export type DestinationStatus = "delivered" | "preview_not_posted" | "not_selected" | "not_offered";

export type DeliveryKind = "written" | "outbox" | "preview";

export interface DestinationPreview {
  kind: DeliveryKind;
  note: string | null;
  title: string;
  text: string;
  payload_sha256: string;
  path: string | null;
}

export interface Destination {
  id: DestinationId;
  label: string;
  status: DestinationStatus;
  preview: DestinationPreview | null;
}

export interface Run {
  format: string;
  run_id: string;
  provenance: Provenance;
  label: string;
  seed: number;
  match_id: string;
  generator: string;
  home: { club_id: string; name: string };
  away: { club_id: string; name: string };
  score: { home: number; away: number };
  bundle: { sha256: string; dir: string; manifest: string };
  state: BundleState;
  states: StateStep[];
  desks: Desk[];
  handoffs: Handoff[];
  gates: Gate[];
  editions: EditionMeta[];
  sentence_types: Record<string, SentenceType>;
  sentence_gates: Record<string, Record<string, GateVerdict>>;
  confirm_rows: ConfirmRow[];
  desk_editor_flags: DeskEditorFlag[];
  approval: Approval;
  /* Optional, read when a pipeline run carries it; the hand-written sample does not. */
  destinations?: Destination[];
  /* Optional the same way: the other steps' flags, never the Desk Editor's (agon.webrecord.flags_by_source). */
  check_flags?: CheckFlag[];
}

export interface Claim {
  type: string;
  value: unknown;
  event_ids?: string[];
  stat?: string;
  subject?: string;
}

export interface Sentence {
  sentence_id: string;
  text: string;
  language: string;
  author: string;
  claims: Claim[];
}

export interface EditionFile {
  edition: string;
  language: string;
  match_id: string;
  sentences: Sentence[];
}

export interface BundleArtifact {
  bytes: number;
  sha256: string;
  language?: string;
  media_type?: string;
  role?: string;
}

export interface BundleManifest {
  schema: string;
  artifacts: Record<string, BundleArtifact>;
  attested_scope: { attested: string[]; gate_cleared: string[] };
  [key: string]: unknown;
}

export interface LoadedEdition {
  meta: EditionMeta;
  data: EditionFile;
}

export interface LoadedRun {
  run: Run;
  manifest: BundleManifest;
  editions: Record<string, LoadedEdition>;
}

export interface RunsData {
  index: RunsIndex;
  runs: Record<string, LoadedRun>;
  defaultRunId: string;
}

/* What every screen starts from: identical on the server and the client, so the
   prerendered page and the first client render agree byte for byte. */
export type InitialData =
  | { status: "ready"; runs: RunsData }
  | { status: "error"; message: string };
