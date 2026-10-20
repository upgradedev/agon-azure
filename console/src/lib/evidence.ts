/*
  The "How Agon works" view: each node of the pipeline with the evidence a
  visitor can open. Three kinds of evidence only, all checkable by a stranger:

  - a file and line in the public repository, as a permalink at one commit of
    main (EVIDENCE_SHA), so the line never moves under the link;
  - a GitHub Actions run on main that finished green;
  - a file on the public feed container the Publisher wrote (the recorded
    run's sealed manifest), which anyone can download and hash.

  tests/evidence.test.ts reads every file at EVIDENCE_SHA with git and fails
  when a cited line no longer holds its snippet, so a link can never point at
  the wrong line. The status line of each node says what exists today: "Ran
  in Azure for the recorded run" only where the recorded run of 2026-10-19
  shows it (evidence/run-c8a19020c98a.executions.json, read from Azure with
  az containerapp job execution list, and the sealed manifest on the feed).
*/
import { REPO_URL } from "./commit";

export const EVIDENCE_SHA = "81b77ce634e772a99936cd09c876dea84051e9e8";

/* The public feed container the Publisher writes, and the recorded run's bundle on it. */
export const FEED_URL = "https://stagonnnk4re3nvfz7o.blob.core.windows.net/feed/";
export const RECORDED_BUNDLE = "c8a19020c98a8de7dab58475bc0b2466129d3de771e30fd8ec519ab2716382dd";
export const RECORDED_EXECUTIONS = "evidence/run-c8a19020c98a.executions.json";
export const DRILL_RUN_ID = 39326468664;

export type EvidenceItem =
  | { kind: "file"; label: string; path: string; line: number; snippet: string }
  | { kind: "run"; label: string; runId: number; workflow: string; commit: string }
  | { kind: "feed"; label: string; path: string };

export type NodeStatus = "azure" | "live" | "ci" | "repo" | "not-deployed";

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
  azure: "Ran in Azure for the recorded run",
  live: "Live on this site",
  ci: "In the repo, tested in CI",
  repo: "In the repo",
  "not-deployed": "Not deployed yet",
};

export function evidenceHref(item: EvidenceItem): string {
  if (item.kind === "file") {
    return `${REPO_URL}/blob/${EVIDENCE_SHA}/${item.path}#L${item.line}`;
  }
  if (item.kind === "feed") {
    return `${FEED_URL}${item.path}`;
  }
  return `${REPO_URL}/actions/runs/${item.runId}`;
}

export function evidenceText(item: EvidenceItem): string {
  if (item.kind === "file") {
    return `${item.path}:${item.line}`;
  }
  if (item.kind === "feed") {
    return `feed/${item.path} on the public feed`;
  }
  return `${item.workflow} run ${item.runId}, commit ${item.commit}, green`;
}

/* The recorded run's own evidence, cited by every node that ran in Azure for it. */
const FEED_MANIFEST: EvidenceItem = { kind: "feed", label: "The recorded run's sealed manifest, as the Publisher published it", path: `${RECORDED_BUNDLE}/manifest.json` };
const DRILL_RUN: EvidenceItem = { kind: "run", label: "The operator's drill that started the recorded run", runId: DRILL_RUN_ID, workflow: "fleet-run", commit: "e852b08" };
const executions = (line: number, snippet: string, label: string): EvidenceItem => ({ kind: "file", label, path: RECORDED_EXECUTIONS, line, snippet });
const GOVERNANCE_RUN: EvidenceItem = { kind: "run", label: "Governance tests on main", runId: 39274209462, workflow: "governance", commit: "96ee47a" };

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
    id: "start",
    stage: "Start",
    title: "Run start",
    status: "azure",
    statusText: `Ran in Azure for the recorded run, started by an operator (drill run ${DRILL_RUN_ID}). No automatic trigger at the final whistle is deployed.`,
    summary:
      "An operator dispatches a run with the fleet-run workflow. The fleet job then runs a Microsoft Agent Framework workflow: ingest the frozen event log, read it, write, check, seal and wait.",
    evidence: [
      { kind: "file", label: "The run is dispatched by hand", path: ".github/workflows/fleet-run.yml", line: 15, snippet: "workflow_dispatch:" },
      { kind: "file", label: "The workflow graph", path: "src/agon/fleet/workflow.py", line: 257, snippet: "def build_workflow(" },
      { kind: "file", label: "The ingest step", path: "src/agon/fleet/workflow.py", line: 71, snippet: "class IngestExecutor(" },
      executions(15, "fleet: ran W-fulltime to the approval pause", "The fleet job's execution for the recorded run, read from Azure"),
      DRILL_RUN,
    ],
  },
  {
    id: "mcp",
    stage: "Read",
    title: "Match facts tool (MCP)",
    status: "ci",
    statusText: "In the repo, tested in CI. Not deployed, and the deployed fleet does not use it.",
    summary:
      "A read-only Model Context Protocol server over the match facts, so a model can quote the log but never change it. The deployed fleet reads the frozen event log in process instead.",
    evidence: [
      { kind: "file", label: "The read-only server", path: "src/agon/mcp/matchfacts.py", line: 37, snippet: "def build_server(" },
      { kind: "file", label: "The tools it exposes", path: "src/agon/mcp/tools.py", line: 86, snippet: "class MatchFactsTools:" },
      { kind: "file", label: "The fleet reads the facts in process", path: "src/agon/fleet/sources.py", line: 38, snippet: "def facts(self) -> MatchFacts:" },
      { kind: "run", label: "MCP tests on main", runId: 39267686243, workflow: "mcp", commit: "d97550a" },
    ],
  },
  {
    id: "interpret",
    stage: "Read",
    title: "Match model",
    status: "ci",
    statusText:
      "In the repo, tested in CI, and the Match Centre shows its numbers. The fleet calls it now, but no published run has used it yet: the recorded run's manifest names a stand-in interpret step, fleet-local-standin/1, that ranks only goals and red cards.",
    summary:
      "Plain code turns the log into xG, xT, PPDA, control vs chaos and win probability, so the writers can cite numbers instead of guessing them.",
    evidence: [
      { kind: "file", label: "Every quantity for one match", path: "src/agon/interpret/__init__.py", line: 27, snippet: "def interpret_match(" },
      { kind: "file", label: "The interpret step the fleet calls now", path: "src/agon/fleet/interpret_port.py", line: 152, snippet: "def default_interpret()" },
      FEED_MANIFEST,
      { kind: "run", label: "Unit tests on main", runId: 39272735205, workflow: "ci", commit: "70eb95e" },
    ],
  },
  {
    id: "foundry",
    stage: "Write",
    title: "Microsoft Foundry agents",
    status: "azure",
    statusText:
      "Ran in Azure for the recorded run: its sealed manifest names the Foundry agents agon-analyst:1, agon-writer:1 and agon-localiser-editor:1, and the translator azure-translator-nmt/text-v3.0.",
    summary:
      "A Foundry analyst agent picks the turning points and a writer agent writes the headline report. Azure AI Translator translates it into Greek, Spanish and French, and a Foundry localiser-editor agent post-edits each translation. The easy-read edition is made by rules in code: a sentence over 20 words, with a listed idiom, a semicolon or brackets becomes a template line. Only the hand-written sample on this site was not written this way.",
    evidence: [
      { kind: "file", label: "The model port the agents run behind", path: "src/agon/agents/foundry.py", line: 94, snippet: "class FoundryModelPort:" },
      { kind: "file", label: "The writer step", path: "src/agon/fleet/workflow.py", line: 105, snippet: "class WriterExecutor(" },
      { kind: "file", label: "Translation, then the localiser-editor's post-edit", path: "src/agon/fleet/steps.py", line: 415, snippet: "NMT, then the localiser-editor's post-edit" },
      { kind: "file", label: "The easy-read rules in code", path: "src/agon/fleet/steps.py", line: 74, snippet: "EASY_READ_MAX_WORDS = 20" },
      { kind: "file", label: "The Foundry project in Bicep", path: "infra/modules/foundry.bicep", line: 89, snippet: "resource project " },
      FEED_MANIFEST,
    ],
  },
  {
    id: "factgate",
    stage: "Check",
    title: "Fact Gate",
    status: "azure",
    statusText:
      "Ran in Azure for the recorded run: its sealed manifest records pass for the headline report and for the other editions. Its verdicts on the hand-written sample are real too.",
    summary:
      "Deterministic code recomputes every number, name and minute a sentence claims from the log, in every language, and every number written in digits must match a claim. Numbers spelled out in words are out of scope by design. On the sample it struck a sentence that said 2 shots on target when the log says 4.",
    evidence: [
      { kind: "file", label: "The gate", path: "src/agon/governance/factgate.py", line: 320, snippet: "def run_factgate(" },
      FEED_MANIFEST,
      GOVERNANCE_RUN,
    ],
  },
  {
    id: "rights",
    stage: "Check",
    title: "Rights Guard",
    status: "azure",
    statusText: "Ran in Azure for the recorded run: its sealed manifest records pass. Its verdicts on the hand-written sample are real too.",
    summary: "Every name in every sentence must be a fictional name on the roster; real club and player names are refused.",
    evidence: [
      { kind: "file", label: "The screen", path: "src/agon/governance/rights.py", line: 297, snippet: "def run_rights(" },
      FEED_MANIFEST,
      GOVERNANCE_RUN,
    ],
  },
  {
    id: "desk",
    stage: "Check",
    title: "Desk Editor",
    status: "azure",
    statusText: "Ran in Azure for the recorded run as a step of the fleet job; it added no flag to that run's card.",
    summary:
      "Challenges claims code cannot check, such as which moment turned the match, and adds flags the officer reads on the approval card; it never removes a flag and never changes which sentences the officer must tick.",
    evidence: [
      { kind: "file", label: "The desk editor step", path: "src/agon/fleet/workflow.py", line: 141, snippet: "class DeskEditorExecutor(" },
      { kind: "file", label: "What it may do: add flags, never clear one", path: "src/agon/fleet/steps.py", line: 373, snippet: "async def desk_editor(" },
      executions(15, "fleet: ran W-fulltime to the approval pause", "The fleet job's execution for the recorded run, read from Azure"),
    ],
  },
  {
    id: "seal",
    stage: "Seal",
    title: "Seal",
    status: "azure",
    statusText: `Ran in Azure for the recorded run: the fleet sealed bundle ${RECORDED_BUNDLE.slice(0, 12)}, the Publisher published it to the public feed, and Verify recomputes its hash here.`,
    summary: "Every edition goes into one canonical manifest, and the bundle is named by the sha256 of that manifest.",
    evidence: [
      { kind: "file", label: "The seal", path: "src/agon/governance/seal.py", line: 125, snippet: "def seal(" },
      FEED_MANIFEST,
      GOVERNANCE_RUN,
    ],
  },
  {
    id: "wait",
    stage: "Wait",
    title: "Durable wait",
    status: "azure",
    statusText:
      "Ran in Azure for the recorded run: the first fleet process ended at the approval pause, and after the decision a new process resumed the workflow from the Cosmos DB checkpoint. Also tested in CI against Cosmos DB.",
    summary:
      "The workflow waits in a checkpoint until a person decides. The wait survives the process ending, and a fresh worker resumes it.",
    evidence: [
      { kind: "file", label: "The approval step", path: "src/agon/fleet/workflow.py", line: 214, snippet: "class ApprovalExecutor(" },
      { kind: "file", label: "Checkpoints in Cosmos DB", path: "src/agon/fleet/checkpoints.py", line: 118, snippet: "async def open_cosmos_storage(" },
      executions(31, "fleet: a new process resumed the workflow from the durable checkpoint", "The resumed fleet process, read from Azure"),
      { kind: "run", label: "Fleet checkpoints against Cosmos DB on main", runId: 39274209627, workflow: "fleet-cosmos", commit: "96ee47a" },
    ],
  },
  {
    id: "approve-teams",
    stage: "Decide",
    title: "Teams approval",
    status: "azure",
    statusText:
      "Ran in Azure for the recorded run: the officer approved on the Teams card, and the decision intake job verified that decision.",
    summary:
      "The officer approves the exact hash on the Microsoft Teams card. A Logic App puts the decision on a queue, and the decision intake job checks it with the one verifier: hash, nonce unused, not expired, approver role, consumed once.",
    evidence: [
      { kind: "file", label: "The Teams card flow (Logic App)", path: "infra/flows.bicep", line: 202, snippet: "name: 'la-agon-teams-approval'" },
      { kind: "file", label: "The decision intake", path: "src/agon/intake/__init__.py", line: 136, snippet: "def handle_message(" },
      { kind: "file", label: "The one verifier", path: "src/agon/governance/verifier.py", line: 419, snippet: "def verify(" },
      executions(23, "intake: verified the officer's Teams decision", "The intake job's execution for the recorded run, read from Azure"),
      GOVERNANCE_RUN,
    ],
  },
  {
    id: "approve-other",
    stage: "Decide",
    title: "Other approval channels",
    status: "not-deployed",
    statusText:
      "Not deployed yet. The Outlook approval email is built and waits for the owner's consent to the Outlook connection; the signed single-use link has no deployed route; this site's stand-in card sends no decision.",
    summary:
      "In the code, an Outlook approval email, a signed single-use link and this site's stand-in card call the same verifier as the Teams card, and an audit fails if any channel skips it.",
    evidence: [
      { kind: "file", label: "The stand-in card channel", path: "src/agon/governance/channels/console.py", line: 56, snippet: "def handle(" },
      { kind: "file", label: "The audit that every channel calls the verifier", path: "src/agon/governance/routes.py", line: 131, snippet: "def audit_channels(" },
      GOVERNANCE_RUN,
    ],
  },
  {
    id: "publish",
    stage: "Publish",
    title: "Publisher",
    status: "azure",
    statusText:
      "Ran in Azure for the recorded run: the Publisher job published the approved bundle to the public feed, with the club website recap, JSON Feed and RSS. This site publishes nothing.",
    summary:
      "Publishes the approved bytes and only those, create-only. Instagram and YouTube get previews that are never posted, and the newsroom and journalist messages are written, not sent. The Publisher code also accepts an erratum bundle that names its parent; no erratum run path is deployed.",
    evidence: [
      { kind: "file", label: "The publisher", path: "src/agon/governance/publisher.py", line: 230, snippet: "class Publisher:" },
      executions(39, "publisher: recomputed the hash, published create-only to the feed", "The Publisher job's execution for the recorded run, read from Azure"),
      FEED_MANIFEST,
      GOVERNANCE_RUN,
    ],
  },
  {
    id: "console",
    stage: "Verify",
    title: "This console",
    status: "live",
    statusText: "Live on this site.",
    summary:
      "A static site on Azure Static Web Apps under a strict content security policy. Verify recomputes in your browser each run's bundle hash and every sealed file its manifest lists, plus each match's event and names files, from this site's copy.",
    evidence: [
      { kind: "file", label: "The static site in Bicep", path: "infra/modules/staticsite.bicep", line: 27, snippet: "resource site " },
      { kind: "run", label: "A deploy of this console on main, its live readiness gate included", runId: 39379852410, workflow: "deploy", commit: "81b77ce" },
    ],
  },
];

/* What already exists for the Data Lab, which has no published data yet. */
export const DATA_LAB_EVIDENCE: EvidenceItem[] = [
  { kind: "file", label: "The realism bands, frozen and hashed before calibration", path: "src/agon/synth/bands.json", line: 2, snippet: "\"header\"" },
  { kind: "file", label: "The dataset card for the synthetic matches", path: "DATASET_CARD.md", line: 1, snippet: "# Agon synthetic match data" },
  { kind: "run", label: "Determinism check on main", runId: 39270968731, workflow: "determinism", commit: "50a8a06" },
];
