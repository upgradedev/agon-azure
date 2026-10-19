/*
  The "How Agon works" view: each node of the pipeline with the evidence a
  visitor can open. Two kinds of evidence only, both checkable by a stranger:

  - a file and line in the public repository, as a permalink at one commit of
    main (EVIDENCE_SHA), so the line never moves under the link;
  - a GitHub Actions run on main that finished green.

  tests/evidence.test.ts reads every file at EVIDENCE_SHA with git and fails
  when a cited line no longer holds its snippet, so a link can never point at
  the wrong line. Nothing here is a claim about a service running: the status
  line of each node says what exists today.
*/
import { REPO_URL } from "./commit";

export const EVIDENCE_SHA = "96ee47abebd8b32c61fc36dbb38ace08b600be51";

export type EvidenceItem =
  | { kind: "file"; label: string; path: string; line: number; snippet: string }
  | { kind: "run"; label: string; runId: number; workflow: string; commit: string };

export type NodeStatus = "live" | "ci" | "repo" | "not-deployed";

export interface PipelineNode {
  id: string;
  stage: string;
  title: string;
  status: NodeStatus;
  statusText: string;
  summary: string;
  evidence: EvidenceItem[];
}

export const STATUS_WORDS: Record<NodeStatus, string> = {
  live: "Live on this site",
  ci: "In the repo, tested in CI",
  repo: "In the repo",
  "not-deployed": "Not deployed yet",
};

export function evidenceHref(item: EvidenceItem): string {
  if (item.kind === "file") {
    return `${REPO_URL}/blob/${EVIDENCE_SHA}/${item.path}#L${item.line}`;
  }
  return `${REPO_URL}/actions/runs/${item.runId}`;
}

export function evidenceText(item: EvidenceItem): string {
  if (item.kind === "file") {
    return `${item.path}:${item.line}`;
  }
  return `${item.workflow} run ${item.runId}, commit ${item.commit}, green`;
}

export const PIPELINE: PipelineNode[] = [
  {
    id: "synth",
    stage: "Data",
    title: "Synthetic match",
    status: "ci",
    statusText: "In the repo, tested in CI. The Match Centre replays its output.",
    summary:
      "A seeded generator plays a fictional match in a fictional league and writes one event per line. The same seed always gives the same bytes, so every number downstream can be checked again.",
    evidence: [
      { kind: "file", label: "The match simulator", path: "src/agon/synth/engine.py", line: 769, snippet: "def simulate_match(" },
      { kind: "file", label: "The web data contract the console reads", path: "src/agon/webdata.py", line: 143, snippet: "def build_web_data(" },
      { kind: "run", label: "Determinism check on main", runId: 39270968731, workflow: "determinism", commit: "50a8a06" },
    ],
  },
  {
    id: "whistle",
    stage: "Trigger",
    title: "Full-time whistle",
    status: "ci",
    statusText: "In the repo, tested in CI. Not connected to this public page.",
    summary:
      "The whistle starts a Microsoft Agent Framework workflow without anyone asking: ingest the frozen event log, read it, write, check, seal and wait.",
    evidence: [
      { kind: "file", label: "The workflow graph", path: "src/agon/fleet/workflow.py", line: 257, snippet: "def build_workflow(" },
      { kind: "file", label: "The ingest step", path: "src/agon/fleet/workflow.py", line: 71, snippet: "class IngestExecutor(" },
      { kind: "run", label: "Fleet workflow tests on main", runId: 39274209217, workflow: "fleet", commit: "96ee47a" },
    ],
  },
  {
    id: "mcp",
    stage: "Read",
    title: "Match facts tool (MCP)",
    status: "ci",
    statusText: "In the repo, tested in CI.",
    summary:
      "Agents read the match only through a read-only Model Context Protocol server, so a model can quote the log but never change it.",
    evidence: [
      { kind: "file", label: "The read-only server", path: "src/agon/mcp/matchfacts.py", line: 37, snippet: "def build_server(" },
      { kind: "file", label: "The tools it exposes", path: "src/agon/mcp/tools.py", line: 86, snippet: "class MatchFactsTools:" },
      { kind: "run", label: "MCP tests on main", runId: 39267686243, workflow: "mcp", commit: "d97550a" },
    ],
  },
  {
    id: "interpret",
    stage: "Read",
    title: "Match model",
    status: "ci",
    statusText: "In the repo, tested in CI.",
    summary:
      "Plain code turns the log into xG, xT, PPDA, control vs chaos and win probability, so the writers cite numbers instead of guessing them.",
    evidence: [
      { kind: "file", label: "Every quantity for one match", path: "src/agon/interpret/__init__.py", line: 27, snippet: "def interpret_match(" },
      { kind: "run", label: "Unit tests on main", runId: 39272735205, workflow: "ci", commit: "70eb95e" },
    ],
  },
  {
    id: "foundry",
    stage: "Write",
    title: "Microsoft Foundry agents",
    status: "repo",
    statusText: "In the repo. The project and model deployments are defined as code.",
    summary:
      "Foundry agents write the headline report, translate it into Greek, Spanish and French and rewrite it as easy-read. The sample run on this site was written by hand instead.",
    evidence: [
      { kind: "file", label: "The model port the writers call", path: "src/agon/agents/foundry.py", line: 94, snippet: "class FoundryModelPort:" },
      { kind: "file", label: "The writer step", path: "src/agon/fleet/workflow.py", line: 105, snippet: "class WriterExecutor(" },
      { kind: "file", label: "The localise step", path: "src/agon/fleet/workflow.py", line: 151, snippet: "class LocaliseExecutor(" },
      { kind: "file", label: "The Foundry project in Bicep", path: "infra/modules/foundry.bicep", line: 89, snippet: "resource project " },
    ],
  },
  {
    id: "factgate",
    stage: "Check",
    title: "Fact Gate",
    status: "ci",
    statusText: "In the repo, tested in CI. Its verdicts on the sample are real.",
    summary:
      "Deterministic code checks every number, name and minute in every language against the log. On the sample it struck a sentence that said 2 shots on target when the log says 4.",
    evidence: [
      { kind: "file", label: "The gate", path: "src/agon/governance/factgate.py", line: 276, snippet: "def run_factgate(" },
      { kind: "run", label: "Governance tests on main", runId: 39274209462, workflow: "governance", commit: "96ee47a" },
    ],
  },
  {
    id: "rights",
    stage: "Check",
    title: "Rights Guard",
    status: "ci",
    statusText: "In the repo, tested in CI. Its verdicts on the sample are real.",
    summary: "Every name in every sentence must be a fictional name on the roster; real club and player names are refused.",
    evidence: [
      { kind: "file", label: "The screen", path: "src/agon/governance/rights.py", line: 297, snippet: "def run_rights(" },
      { kind: "run", label: "Governance tests on main", runId: 39274209462, workflow: "governance", commit: "96ee47a" },
    ],
  },
  {
    id: "desk",
    stage: "Check",
    title: "Desk Editor",
    status: "ci",
    statusText: "In the repo, tested in CI.",
    summary:
      "Flags the sentences code cannot check, such as a medical assessment, so a person confirms them on the approval card.",
    evidence: [{ kind: "file", label: "The desk editor step", path: "src/agon/fleet/workflow.py", line: 141, snippet: "class DeskEditorExecutor(" }],
  },
  {
    id: "seal",
    stage: "Seal",
    title: "Seal",
    status: "ci",
    statusText: "In the repo. The sample bundle on this site was sealed by it, and Verify recomputes it.",
    summary: "Every edition goes into one canonical manifest, and the bundle is named by the sha256 of that manifest.",
    evidence: [
      { kind: "file", label: "The seal", path: "src/agon/governance/seal.py", line: 108, snippet: "def seal(" },
      { kind: "run", label: "Governance tests on main", runId: 39274209462, workflow: "governance", commit: "96ee47a" },
    ],
  },
  {
    id: "wait",
    stage: "Wait",
    title: "Durable wait",
    status: "ci",
    statusText: "In the repo, tested in CI against Cosmos DB.",
    summary:
      "The workflow waits in a checkpoint until a person decides. The wait survives the process being killed, and a fresh worker resumes it.",
    evidence: [
      { kind: "file", label: "The approval step", path: "src/agon/fleet/workflow.py", line: 214, snippet: "class ApprovalExecutor(" },
      { kind: "file", label: "Checkpoints in Cosmos DB", path: "src/agon/fleet/checkpoints.py", line: 118, snippet: "async def open_cosmos_storage(" },
      { kind: "run", label: "Fleet checkpoints against Cosmos DB on main", runId: 39274209627, workflow: "fleet-cosmos", commit: "96ee47a" },
    ],
  },
  {
    id: "approve",
    stage: "Decide",
    title: "Approval channels",
    status: "not-deployed",
    statusText: "In the repo, tested in CI. The decision endpoint is not deployed yet.",
    summary:
      "The officer approves the exact hash in Microsoft Teams, by Outlook approval email, by a signed single-use link or on this site's stand-in card. Every channel calls the same verifier.",
    evidence: [
      { kind: "file", label: "The one verifier", path: "src/agon/governance/verifier.py", line: 195, snippet: "def verify(" },
      { kind: "file", label: "The stand-in card channel", path: "src/agon/governance/channels/console.py", line: 48, snippet: "def handle(" },
      { kind: "file", label: "The audit that every channel calls the verifier", path: "src/agon/governance/routes.py", line: 131, snippet: "def audit_channels(" },
      { kind: "file", label: "The decision intake", path: "src/agon/intake/__init__.py", line: 133, snippet: "def handle_message(" },
      { kind: "run", label: "Governance tests on main", runId: 39274209462, workflow: "governance", commit: "96ee47a" },
    ],
  },
  {
    id: "publish",
    stage: "Publish",
    title: "Publisher",
    status: "ci",
    statusText: "In the repo, tested in CI. Nothing has been published from this site.",
    summary: "Publishes the approved bytes and only those, create-only, so a correction becomes a new bundle that names its parent.",
    evidence: [
      { kind: "file", label: "The publisher", path: "src/agon/governance/publisher.py", line: 81, snippet: "class Publisher:" },
      { kind: "run", label: "Governance tests on main", runId: 39274209462, workflow: "governance", commit: "96ee47a" },
    ],
  },
  {
    id: "console",
    stage: "Verify",
    title: "This console",
    status: "live",
    statusText: "Live on this site.",
    summary:
      "A static site on Azure Static Web Apps under a strict content security policy. Verify recomputes every hash in your browser.",
    evidence: [
      { kind: "file", label: "The static site in Bicep", path: "infra/modules/staticsite.bicep", line: 27, snippet: "resource site " },
      { kind: "run", label: "A deploy of this site on main", runId: 39245993697, workflow: "deploy", commit: "457bd7e" },
      { kind: "run", label: "The live readiness gate after that deploy", runId: 39246024312, workflow: "readiness", commit: "457bd7e" },
    ],
  },
];

/* What already exists for the Data Lab, which has no published data yet. */
export const DATA_LAB_EVIDENCE: EvidenceItem[] = [
  { kind: "file", label: "The realism bands, frozen and hashed before calibration", path: "src/agon/synth/bands.json", line: 2, snippet: "\"header\"" },
  { kind: "file", label: "The dataset card for the synthetic matches", path: "DATASET_CARD.md", line: 1, snippet: "# Agon synthetic match data" },
  { kind: "run", label: "Determinism check on main", runId: 39270968731, workflow: "determinism", commit: "50a8a06" },
];
