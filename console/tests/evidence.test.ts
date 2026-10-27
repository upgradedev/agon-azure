/*
  Every file-and-line link on "How Agon works" and the Data Lab must point at
  a line that holds what the label says. The files are read at EVIDENCE_SHA
  with git (the console workflow checks out the full history), so a later
  change on main can never move a line under its link.

  A node marked "Ran in Azure for the recorded run" must cite that run: its
  job executions read from Azure, its sealed manifest on the public feed or
  the operator's drill that started it. The bundle hash it names is held to
  the committed copy of that manifest, byte for byte.

  That recorded run must be the run every other screen opens by default, the
  first in web/runs/index.json, and the agents, interpret step, verdicts and
  flags the status lines name must be the ones its sealed files record, so
  this view can never describe another run than the rest of the site.
*/
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DATA_LAB_EVIDENCE,
  DRILL_RUN_ID,
  EVIDENCE_SHA,
  FEED_URL,
  PIPELINE,
  RECORDED_BUNDLE,
  RECORDED_DATE,
  RECORDED_EXECUTIONS,
  RECORDED_RUN_ID,
  evidenceHref,
  type EvidenceItem,
} from "../src/lib/evidence";
import { REPO, WEB, readJson } from "./helpers";

function fileAt(path: string): string {
  return execFileSync("git", ["show", `${EVIDENCE_SHA}:${path}`], { cwd: REPO, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
}

const items: EvidenceItem[] = [...PIPELINE.flatMap((node) => node.evidence), ...DATA_LAB_EVIDENCE];

describe("evidence links", () => {
  it("cite only lines that hold their snippet at the pinned commit", () => {
    const files = items.filter((item): item is Extract<EvidenceItem, { kind: "file" }> => item.kind === "file");
    expect(files.length).toBeGreaterThan(15);
    for (const item of files) {
      const lines = fileAt(item.path).split("\n");
      expect(lines.length, item.path).toBeGreaterThanOrEqual(item.line);
      expect(lines[item.line - 1], `${item.path}:${item.line}`).toContain(item.snippet);
    }
  });

  it("link runs and files on the public repository, and feed files on the public feed container, only", () => {
    expect(FEED_URL).toMatch(/^https:\/\/[a-z0-9]+\.blob\.core\.windows\.net\/feed\/$/);
    for (const item of items) {
      if (item.kind === "feed") {
        expect(evidenceHref(item)).toBe(`${FEED_URL}${item.path}`);
        expect(item.path).toMatch(/^[0-9a-f]{64}\/manifest\.json$/);
      } else {
        expect(evidenceHref(item)).toMatch(/^https:\/\/github\.com\/upgradedev\/agon-azure\/(?:blob\/[0-9a-f]{40}\/|actions\/runs\/[0-9]+$)/);
      }
    }
  });

  it("give every pipeline node a status sentence and at least one piece of evidence", () => {
    for (const node of PIPELINE) {
      expect(node.evidence.length, node.id).toBeGreaterThan(0);
      expect(node.statusText.endsWith("."), node.id).toBe(true);
      expect(node.summary.endsWith("."), node.id).toBe(true);
    }
  });

  it("make every node that ran in Azure cite the recorded run, and name that run's real bundle", () => {
    const committed = readFileSync(join(REPO, "web", "runs", `run-${RECORDED_BUNDLE.slice(0, 12)}`, "manifest.json"));
    expect(createHash("sha256").update(committed).digest("hex")).toBe(RECORDED_BUNDLE);
    const executions = fileAt(RECORDED_EXECUTIONS);
    expect(executions).toContain(`"bundle_sha256": "${RECORDED_BUNDLE}"`);
    expect(executions).toContain(`-${DRILL_RUN_ID}"`);
    const azure = PIPELINE.filter((node) => node.status === "azure");
    expect(azure.length).toBeGreaterThan(5);
    for (const node of azure) {
      const cites = node.evidence.some(
        (item) =>
          (item.kind === "file" && item.path === RECORDED_EXECUTIONS) ||
          (item.kind === "feed" && item.path === `${RECORDED_BUNDLE}/manifest.json`) ||
          (item.kind === "run" && item.runId === DRILL_RUN_ID),
      );
      expect(cites, node.id).toBe(true);
    }
  });

  it("describe the run the other screens open by default: the first in web/runs/index.json", () => {
    const first = readJson<{ runs: { run_id: string; file: string; provenance: string }[] }>(join(WEB, "runs", "index.json")).runs[0];
    expect(first.run_id).toBe(RECORDED_RUN_ID);
    expect(first.provenance).toBe("pipeline");
    const run = readJson<{ run_id: string; label: string; bundle: { sha256: string }; approval: { approved_bundle_sha256: string; channel: string } }>(
      join(WEB, "runs", first.file),
    );
    expect(run.run_id).toBe(RECORDED_RUN_ID);
    expect(run.bundle.sha256).toBe(RECORDED_BUNDLE);
    expect(run.approval.approved_bundle_sha256).toBe(RECORDED_BUNDLE);
    expect(run.approval.channel).toBe("teams");
    expect(run.label.startsWith(`Recorded run, ${RECORDED_DATE}, `)).toBe(true);
    const executions = JSON.parse(fileAt(RECORDED_EXECUTIONS)) as {
      run_id: string;
      recorded_run: string;
      bundle_sha256: string;
      executions: { job: string; start: string; end: string }[];
    };
    expect(executions.recorded_run).toBe(RECORDED_RUN_ID);
    expect(executions.bundle_sha256).toBe(RECORDED_BUNDLE);
    expect(executions.run_id.endsWith(`-${DRILL_RUN_ID}`)).toBe(true);
    for (const row of executions.executions) {
      expect(row.start.startsWith(RECORDED_DATE), row.job).toBe(true);
    }
    // The worker ended at the pause and a new process resumed: two fleet executions, one after the other.
    const fleet = executions.executions.filter((row) => row.job === "job-agon-fleet");
    expect(fleet.length).toBe(2);
    expect(Date.parse(fleet[1].start)).toBeGreaterThan(Date.parse(fleet[0].end));
    expect(executions.executions.filter((row) => row.job === "job-agon-intake").length).toBe(1);
    expect(executions.executions.filter((row) => row.job === "job-agon-publisher").length).toBe(1);
  });

  it("name only what the recorded run's sealed files record", () => {
    const dir = join(WEB, "runs", RECORDED_RUN_ID);
    const manifest = readJson<{ deployment_ids: Record<string, string>; gate_verdicts: Record<string, string>; artifacts: Record<string, { sha256: string }> }>(
      join(dir, "manifest.json"),
    );
    const node = (id: string) => PIPELINE.find((n) => n.id === id)?.statusText ?? "";
    for (const key of ["agent.analyst", "agent.writer", "agent.localiser_editor", "translator"]) {
      expect(node("foundry"), key).toContain(manifest.deployment_ids[key]);
    }
    expect(node("interpret")).toContain(`its sealed manifest names the interpret step ${manifest.deployment_ids.interpret},`);
    expect(manifest.gate_verdicts).toEqual({ factgate: "pass", factgate_lang: "pass", rights: "pass" });
    const cardBytes = readFileSync(join(dir, "card.json"));
    expect(createHash("sha256").update(cardBytes).digest("hex")).toBe(manifest.artifacts["card.json"].sha256);
    const card = JSON.parse(cardBytes.toString("utf8")) as { flags: { by: string; code: string; sentence_id?: string }[] };
    expect(card.flags.filter((flag) => flag.by === "desk_editor")).toEqual([]);
    const onSentences = card.flags.filter((flag) => flag.sentence_id);
    expect(onSentences.map((flag) => [flag.by, flag.sentence_id])).toEqual([["factgate", "s7"]]);
    expect(card.flags.some((flag) => flag.by === "factgate" && flag.code === "factgate_retry")).toBe(true);
    expect(node("desk")).toContain("the one sentence flag there, on s7, was raised by the Fact Gate");
    expect(node("factgate")).toContain("sent the draft back to the Writer once and flagged s7");
  });

  it("never claim what is not deployed (wording the readiness audit of 2026-10-20 found)", () => {
    const text = PIPELINE.map((node) => `${node.title} ${node.statusText} ${node.summary}`).join("\n");
    for (const stale of [
      "without anyone asking",
      "only through a read-only Model Context Protocol server",
      "Every channel calls the same verifier",
      "The decision endpoint is not deployed",
      "Nothing has been published from this site",
      "written by hand instead",
      "a correction becomes a new bundle",
      "Verify recomputes every hash",
    ]) {
      expect(text, stale).not.toContain(stale);
    }
    const mcp = PIPELINE.find((node) => node.id === "mcp");
    expect(mcp?.statusText).toContain("Not deployed");
    const start = PIPELINE.find((node) => node.id === "start");
    expect(start?.statusText).toContain("No automatic trigger at the final whistle is deployed.");
  });
});
